"""improve loop: distillation, memory, and the red->green metric climb."""
import asyncio
import json

import pytest

from env.app import create_app
from loop import config, improve
from loop.distill import distill
from loop.env_client import EnvClient
from loop.memory import MemoryStore
from loop.metrics import load_metrics
from loop.sim_agent import SimAgent


@pytest.fixture()
def factory(tmp_path, monkeypatch):
    monkeypatch.setenv("ENV_SESSIONS_DIR", str(tmp_path / "sessions"))
    app = create_app()
    return lambda: EnvClient.for_app(app)


@pytest.fixture()
def task():
    return config.load_tasks()["t2"]


@pytest.fixture()
def spec():
    return config.load_spec()


def test_heuristic_distill_finds_pagination(task):
    successes = [{"task_id": "t2", "seed": 0, "reason": "ok",
                  "actions": ["/s/x/", "/api/flights?page=1",
                              "/api/flights?page=2", "/api/flights?page=3",
                              "/flights/7", "POST /flights/7/book"]}]
    notes = distill("", successes)
    assert "paginate" in notes.lower()
    assert "detail page" in notes.lower()


def test_distill_no_successes_keeps_notes():
    assert distill("keep me", []) == "keep me"


def test_distill_learns_from_failure_feedback():
    """Lucky page-1 successes teach nothing — but verifier reasons on the
    failures say the expected item differed while nobody paginated."""
    lucky = [{"task_id": "t2", "seed": 1, "reason": "booked FL100 at $171",
              "actions": ["/s/x/", "/api/flights?page=1", "/flights/1",
                          "POST /flights/1/book"]}]
    failures = [{"task_id": "t2", "seed": 0,
                 "reason": "booked FL102 ($460), expected FL123 ($172)",
                 "actions": ["/s/x/", "/api/flights?page=1", "/flights/3",
                             "POST /flights/3/book"]}]
    notes = distill("", lucky, failures=failures)
    assert "paginate" in notes.lower()
    # rule written once even with several failures
    assert notes.lower().count("paginate through all") == 1


def test_infrastructure_failures_do_not_teach_pagination():
    notes = distill("", [], failures=[{"reason": "error: expected HTTP 200", "actions": []}])
    assert "paginate" not in notes.lower()


def test_distill_llm_path(task):
    called = {}

    def fake_llm(prompt):
        called["prompt"] = prompt
        return "# Skill notes\n- llm wrote this\n"

    notes = distill("old", [{"task_id": "t", "seed": 0, "reason": "ok",
                             "actions": []}], llm=fake_llm)
    assert "llm wrote this" in notes
    assert '"task_id": "t"' in called["prompt"]


def test_memory_store_roundtrip(tmp_path):
    store = MemoryStore(str(tmp_path / "skill_notes.md"))
    assert store.load() == ""
    store.save("# notes\n")
    assert store.load() == "# notes\n"
    assert store.hash() == MemoryStore(store.path).hash()


def test_improve_climbs_on_held_out_seeds(factory, task, spec, tmp_path):
    """sim:auto + distilled notes = the red->green climb, deterministically."""
    memory = MemoryStore(str(tmp_path / "notes.md"))
    metrics = str(tmp_path / "metrics.json")
    rollouts = str(tmp_path / "rollouts.jsonl")
    agent = SimAgent(spec, "auto")
    history = asyncio.run(improve.improve(
        factory, agent, task, rounds=2, memory=memory, llm=None,
        train_seeds=range(0, 8), eval_seeds=range(100, 104),
        eval_overrides={"popup": "1", "labels": "3"},
        rollouts_path=rollouts, metrics_path=metrics))
    assert history[0]["notes_changed"] is True
    assert "paginate" in memory.load().lower()
    assert history[-1]["eval_success_rate"] == 1.0

    m = load_metrics(metrics)
    assert [e["round"] for e in m["metrics"]] == [1, 2]
    assert m["metrics"][-1]["success_rate"] == 1.0

    lines = [json.loads(l) for l in open(rollouts)]
    assert all(l["phase"] in ("train", "eval") for l in lines)


def test_agent_config_written(tmp_path, spec, task):
    memory = MemoryStore(str(tmp_path / "notes.md"))
    cfg = improve.write_agent_config(
        str(tmp_path / "agent_config.json"), SimAgent(spec, "auto"), task,
        memory, [{"round": 1, "eval_success_rate": 1.0}],
        range(5), range(100, 105), {"popup": "1"})
    loaded = json.load(open(tmp_path / "agent_config.json"))
    assert loaded["agent"] == "sim:auto"
    assert loaded["final_eval_success_rate"] == 1.0
