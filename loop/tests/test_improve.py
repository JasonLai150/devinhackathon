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
    assert [e["round"] for e in m["metrics"]] == [0, 1, 2]
    assert len({e["evaluation_id"] for e in m["metrics"]}) == 1
    assert all(e["seeds"] == [100, 101, 102, 103] and e["n"] == 4
               for e in m["metrics"])
    assert m["metrics"][0]["success_rate"] < m["metrics"][-1]["success_rate"]
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


def test_strict_distillation_excludes_evaluation_and_failures():
    from loop.distill import distill_verified
    record = {"task_id": "shop-cheapest", "seed": 0, "passed": True,
              "phase": "train", "reason": "verified", "actions": ["read page 2"]}
    prompts = []
    def llm(prompt):
        prompts.append(prompt)
        return "# Skills\n- Compare visible results before choosing."
    assert "Compare" in distill_verified("", [record], llm)
    assert "verified" in prompts[0]
    with pytest.raises(ValueError):
        distill_verified("", [{**record, "phase": "eval"}], llm)
    with pytest.raises(ValueError):
        distill_verified("", [{**record, "passed": False}], llm)
    assert len(prompts) == 1


def test_strict_zero_successes_keeps_memory_and_curve(factory, task, spec, tmp_path):
    def must_not_distill(prompt):
        raise AssertionError("no successful training evidence")
    memory = MemoryStore(str(tmp_path / "notes.md"))
    path = str(tmp_path / "metrics.json")
    history = asyncio.run(improve.improve(
        factory, SimAgent(spec, "naive"), task, 2, strict=True, attempts=2,
        memory=memory, llm=must_not_distill, train_seeds=[0, 2], eval_seeds=[100, 101],
        metrics_path=path))
    assert memory.load() == ""
    assert all(h["successes_distilled"] == 0 and not h["notes_changed"] for h in history)
    entries = load_metrics(path)["metrics"]
    assert [m["round"] for m in entries] == [0, 1, 2]
    assert len({m["success_rate"] for m in entries}) == 1
    assert len({m["evaluation_id"] for m in entries}) == 1


def test_strict_distiller_error_does_not_fall_back():
    from loop.distill import distill_verified
    def failing(prompt):
        raise RuntimeError("provider unavailable")
    with pytest.raises(RuntimeError, match="provider unavailable"):
        distill_verified("", [{"task_id": "t", "seed": 1, "passed": True,
                               "phase": "train", "reason": "ok", "actions": []}], failing)


def test_evaluation_overlap_rejected_before_rollouts(factory, task, spec):
    with pytest.raises(ValueError, match="disjoint"):
        asyncio.run(improve.improve(factory, SimAgent(spec), task, 1,
                                    train_seeds=[0, 1], eval_seeds=[1, 2]))
    assert factory().list_sessions() == []


def test_wilson_interval_retains_uncertainty_at_extremes():
    from loop.metrics import wilson_interval
    assert wilson_interval(0, 10)[1] > 0.25
    assert wilson_interval(10, 10)[0] < 0.75


@pytest.mark.parametrize("candidate_works", [False, True])
def test_exploration_memory_needs_verified_training_success(
        factory, task, spec, tmp_path, candidate_works):
    memory = MemoryStore(str(tmp_path / "notes.md"))
    prompts = []
    def llm(prompt):
        prompts.append(prompt)
        assert '"seed": 100' not in prompt and '"seed": 101' not in prompt
        if prompt.startswith("Propose"):
            return "# Unverified hypothesis\n- " + ("paginate" if candidate_works else "choose immediately")
        return "# Verified skill\n- paginate through the result pages."
    history = asyncio.run(improve.improve(
        factory, SimAgent(spec, "auto"), task, 1, strict=True, explore=True,
        llm=llm, memory=memory, train_seeds=[0, 2], eval_seeds=[100, 101]))
    assert history[0]["exploration"]["proposed"]
    if candidate_works:
        assert history[0]["successes_distilled"] == 2
        assert memory.load().startswith("# Verified skill")
        assert len(prompts) == 2
    else:
        assert history[0]["successes_distilled"] == 0
        assert memory.load() == ""
        assert len(prompts) == 1


def test_exploration_proposal_omits_verifier_answers():
    from loop.distill import propose_skill
    prompts = []
    def llm(prompt):
        prompts.append(prompt)
        return "# Hypothesis\n- Inspect additional results."
    failure = {"passed": False, "phase": "train", "reason": "expected SECRET_ANSWER",
               "actions": ["read page 1", "select item"]}
    propose_skill("", {"instruction": "Find the cheapest item."}, [failure], llm)
    assert "SECRET_ANSWER" not in prompts[0]
    assert "read page 1" in prompts[0]


def test_provider_errors_invalidate_evaluation_before_training(
        factory, task, spec, tmp_path, monkeypatch):
    calls = []
    async def unavailable(*args, **kwargs):
        calls.append(kwargs["phase"])
        return [{"passed": False, "reason": "error: API quota exhausted",
                 "error_kind": "provider"}]
    monkeypatch.setattr(improve.runner, "run_round", unavailable)
    path = str(tmp_path / "metrics.json")
    with pytest.raises(RuntimeError, match="invalidated evaluation"):
        asyncio.run(improve.improve(
            factory, SimAgent(spec, "auto"), task, 1, strict=True,
            llm=lambda prompt: "must not run",
            memory=MemoryStore(str(tmp_path / "notes.md")),
            train_seeds=[0], eval_seeds=[100], metrics_path=path))
    assert calls == ["eval"]
    metric = load_metrics(path)["metrics"][0]
    assert metric["provider_errors"] == 1 and not metric["valid"]


def test_continuation_rejects_changed_evaluation_set(factory, task, spec, tmp_path):
    path = str(tmp_path / "metrics.json")
    memory = MemoryStore(str(tmp_path / "notes.md"))
    agent = SimAgent(spec, "auto")
    asyncio.run(improve.improve(factory, agent, task, 1, memory=memory, llm=None,
                               train_seeds=[0, 2], eval_seeds=[100, 101], metrics_path=path))
    before = load_metrics(path)
    with pytest.raises(ValueError, match="same valid evaluation baseline"):
        asyncio.run(improve.improve(
            factory, agent, task, 1, memory=memory, llm=None, start_round=1,
            train_seeds=[0, 2], eval_seeds=[102, 103], metrics_path=path))
    assert load_metrics(path) == before
