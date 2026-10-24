"""SimAgent strategies against the real env, in-process."""
import asyncio

import pytest

from env.app import create_app
from loop import config
from loop.env_client import EnvClient
from loop.sim_agent import SimAgent


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ENV_SESSIONS_DIR", str(tmp_path / "sessions"))
    return EnvClient.for_app(create_app())


@pytest.fixture()
def spec():
    return config.load_spec()


@pytest.fixture()
def tasks():
    return config.load_tasks()


def episode(client, agent, task, seed, notes=""):
    session = client.reset(seed=seed, popup=0)
    res = asyncio.run(agent.run(client, task, session, skill_notes=notes))
    verdict = client.verify(task["id"], session["session_id"])
    return res, verdict


def test_smart_sweeps_t2(client, spec, tasks):
    agent = SimAgent(spec, "smart")
    for seed in range(10):
        _, verdict = episode(client, agent, tasks["t2"], seed)
        assert verdict["passed"], f"seed {seed}: {verdict['reason']}"


def test_naive_reproduces_pagination_failure(client, spec, tasks):
    agent = SimAgent(spec, "naive")
    verdicts = [episode(client, agent, tasks["t2"], s)[1] for s in range(10)]
    passed = sum(v["passed"] for v in verdicts)
    assert 0 < passed < 10, f"naive went {passed}/10 — not a partial failure"
    fails = [v["reason"] for v in verdicts if not v["passed"]]
    assert all("expected" in r for r in fails)


def test_naive_fetches_only_page_one(client, spec, tasks):
    agent = SimAgent(spec, "naive")
    res, _ = episode(client, agent, tasks["t2"], 0)
    assert any("page=1" in a for a in res.actions)
    assert not any("page=2" in a for a in res.actions)


def test_auto_flips_on_skill_notes(client, spec, tasks):
    agent = SimAgent(spec, "auto")
    seed = next(s for s in range(10)
                if not episode(client, SimAgent(spec, "naive"),
                               tasks["t2"], s)[1]["passed"])
    _, without = episode(client, agent, tasks["t2"], seed)
    assert not without["passed"]
    _, with_notes = episode(client, agent, tasks["t2"], seed,
                            notes="Always paginate through all pages.")
    assert with_notes["passed"], with_notes["reason"]


def test_agent_id_names_strategy(spec):
    assert SimAgent(spec, "smart").agent_id == "sim:smart"
