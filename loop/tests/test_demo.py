"""demo.run: the whole pipeline end-to-end, in-process."""
import asyncio
import json
import os
import threading

import pytest

from env.app import create_app
from loop import config, demo
from loop.env_client import EnvClient


@pytest.fixture()
def factory(tmp_path, monkeypatch):
    monkeypatch.setenv("ENV_SESSIONS_DIR", str(tmp_path / "sessions"))
    app = create_app()
    return lambda: EnvClient.for_app(app)


def test_full_pipeline(factory, tmp_path, monkeypatch):
    monkeypatch.setenv("LOOP_ARTIFACTS", str(tmp_path / "artifacts"))
    spec = config.load_spec()
    task = config.load_tasks()["t2"]
    summary = asyncio.run(demo.run(
        factory, spec, task, rounds=2,
        train_seeds=range(0, 8), eval_seeds=range(100, 104),
        repro_seeds=range(0, 8)))

    assert summary["repro"]["reproduced"] is True
    assert summary["repro"]["baseline_success_rate"] < 1.0
    assert summary["improve"][-1]["eval_success_rate"] == 1.0
    assert summary["transfer"]["passed"] is True

    # every contract artifact exists and is well-formed
    art = tmp_path / "artifacts"
    report = json.load(open(art / "repro_report.json"))
    assert report["signature_failures"] == report["failures"] > 0
    metrics = json.load(open(art / "metrics.json"))["metrics"]
    rates = {m["round"]: m["success_rate"] for m in metrics}
    assert rates[0] < 1.0 and rates[max(rates)] == 1.0
    rollouts = [json.loads(l) for l in open(art / "rollouts.jsonl")]
    assert {r["phase"] for r in rollouts} >= {"repro", "train", "eval",
                                             "transfer"}
    cfg = json.load(open(art / "agent_config.json"))
    assert cfg["final_eval_success_rate"] == 1.0
    assert "paginate" in (art / "skill_notes.md").read_text().lower()


def test_a_spec_through_b_generator_and_c_loop(tmp_path, monkeypatch):
    from env.generate import generate
    from loop.agent import make_agent
    from loop import runner

    monkeypatch.setenv("ENV_SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("LOOP_ARTIFACTS", str(tmp_path / "artifacts"))
    spec_path = "fixtures/env_spec.json"
    component = tmp_path / "component"
    assert generate(spec_path, str(component), llm=None)["path"] == "verbatim"
    app = create_app(spec_path, str(component))
    factory = lambda: EnvClient.for_app(app)
    spec = config.load_spec(spec_path)
    tasks = config.load_tasks(spec=spec)
    summary = asyncio.run(demo.run(
        factory, spec, tasks["shop-cheapest"], rounds=1,
        train_seeds=range(3), eval_seeds=range(100, 103), repro_seeds=range(3)))
    assert summary["repro"]["baseline_success_rate"] == 0
    assert summary["repro"]["reproduced"]
    assert summary["improve"][0]["eval_success_rate"] == 1
    assert summary["transfer"]["passed"]
    for task in tasks.values():
        records = asyncio.run(runner.run_round(
            factory, make_agent("sim:smart", spec), task, [42, 103], 1))
        assert all(r["passed"] for r in records), records


@pytest.mark.skipif(os.environ.get("RUN_BROWSER_TESTS") != "1",
                    reason="set RUN_BROWSER_TESTS=1 after installing Chromium")
def test_shared_browser_strategy_reproduces_and_fixes_shop(tmp_path, monkeypatch):
    pytest.importorskip("playwright")
    from werkzeug.serving import make_server
    from capture import agents
    from env.generate import generate
    from loop import runner, repro_check
    from loop.agent import make_agent

    monkeypatch.setenv("ENV_SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.setattr(agents, "_SETTLE_MS", 50)
    component = tmp_path / "component"
    generate("fixtures/env_spec.json", str(component), llm=None)
    app = create_app("fixtures/env_spec.json", str(component))
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        factory = lambda: EnvClient(f"http://127.0.0.1:{server.server_port}")
        spec = config.load_spec("fixtures/env_spec.json")
        task = config.load_tasks(spec=spec)["shop-cheapest"]
        agent = make_agent("browser:auto", spec)
        baseline = asyncio.run(repro_check.repro(
            factory, agent, task, [42], spec, overrides={"popup": 1, "labels": 3}))
        assert baseline[0]["signature_failure"], baseline
        fixed = asyncio.run(runner.run_round(
            factory, agent, task, [42, 103], 1,
            overrides={"popup": 1, "labels": 3}, skill_notes="Always paginate through all pages."))
        assert all(r["passed"] for r in fixed), fixed
    finally:
        server.shutdown()
        thread.join()
