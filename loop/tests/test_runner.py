"""runner.py: parallel episodes, rollouts.jsonl shape, baseline cache,
error paths — all in-process."""
import asyncio
import json

import pytest

from env.app import create_app
from loop import config, runner
from loop.agent import Agent, EpisodeResult
from loop.env_client import EnvClient
from loop.sim_agent import SimAgent


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ENV_SESSIONS_DIR", str(tmp_path / "sessions"))
    app = create_app()
    return EnvClient.for_app(app), (lambda: EnvClient.for_app(app))


@pytest.fixture()
def task():
    return config.load_tasks()["t2"]


class CrashingAgent(Agent):
    agent_id = "crash"

    async def run(self, env, task, session, skill_notes=""):
        raise RuntimeError("boom")


def test_round_produces_contract_records(env, task):
    _, factory = env
    agent = SimAgent(config.load_spec(), "naive")
    recs = asyncio.run(runner.run_round(factory, agent, task, range(10), 0))
    assert len(recs) == 10
    for r in recs:
        for k in runner.ROLLOUT_KEYS:
            assert k in r, f"missing {k}"
        assert r["round"] == 0 and r["task_id"] == "t2"
        assert r["session_id"].startswith(("s",)) or r["session_id"]
    assert 0 < sum(r["passed"] for r in recs) < 10


def test_append_rollouts_jsonl(env, task, tmp_path):
    _, factory = env
    agent = SimAgent(config.load_spec(), "smart")
    recs = asyncio.run(runner.run_round(factory, agent, task, range(3), 1))
    out = tmp_path / "rollouts.jsonl"
    runner.append_rollouts(str(out), recs)
    runner.append_rollouts(str(out), recs)  # append, not overwrite
    lines = [json.loads(l) for l in out.read_text().splitlines()]
    assert len(lines) == 6
    assert lines[0]["task_id"] == "t2" and lines[0]["round"] == 1


def test_baseline_cache_reuses_records(env, task, tmp_path):
    _, factory = env
    agent = SimAgent(config.load_spec(), "smart")
    cache_path = str(tmp_path / "cache.json")
    cache = {}
    r1 = asyncio.run(runner.run_round(factory, agent, task, range(3), 0,
                                      cache=cache, cache_path=cache_path))
    cache2 = runner.load_cache(cache_path)
    r2 = asyncio.run(runner.run_round(factory, agent, task, range(3), 0,
                                      cache=cache2, cache_path=cache_path))
    assert [r["session_id"] for r in r1] == [r["session_id"] for r in r2]


def test_memory_hash_changes_cache_key():
    a = runner.cache_key("t2", 0, "sim:auto", "")
    b = runner.cache_key("t2", 0, "sim:auto", "paginate everything")
    assert a != b


def test_cache_respects_overrides_and_session_lifetime(env, task):
    from env import sessions
    client, factory = env
    agent = SimAgent(config.load_spec(), "smart")
    cache = {}
    def run(popup):
        return asyncio.run(runner.run_round(
            factory, agent, task, [42], 0, overrides={"popup": popup}, cache=cache))[0]
    first = run(0)
    second = run(1)
    assert first["session_id"] != second["session_id"]
    assert client.get_state(second["session_id"])["meta"]["popup"] is True
    sessions.destroy(second["session_id"])
    third = run(1)
    assert third["session_id"] != second["session_id"]


def test_agent_crash_is_a_failed_record(env, task):
    _, factory = env
    recs = asyncio.run(runner.run_round(factory, CrashingAgent(), task,
                                        range(2), 0))
    assert all(r["passed"] is False and r["reason"].startswith("error: boom")
               for r in recs)


def test_episode_timeout_is_a_failed_record(env, task):
    _, factory = env

    class SlowAgent(Agent):
        agent_id = "slow"

        async def run(self, env, task, session, skill_notes=""):
            await asyncio.sleep(5)

    recs = asyncio.run(runner.run_round(factory, SlowAgent(), task, range(1),
                                        0, timeout=0.1))
    assert recs[0]["passed"] is False and "timeout" in recs[0]["reason"]
