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


def test_experiment_preflight_without_keys_does_not_create_results(tmp_path, monkeypatch):
    from loop.experiment import main
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CAPTURE_LLM_PROVIDER"):
        monkeypatch.delenv(name, raising=False)
    assert main(["--preflight", "--out", str(tmp_path)]) == 2
    check = json.loads((tmp_path / "preflight.json").read_text())
    assert not check["ready"]
    assert not (tmp_path / "metrics.json").exists()
    assert not (tmp_path / "run_manifest.json").exists()


def test_model_selection_is_frozen(monkeypatch):
    from capture import llm
    monkeypatch.setenv("OPENAI_API_KEY", "test-placeholder")
    chosen = llm.resolve_config("openai", "chosen-model", 0.4)
    payloads = []
    def post(url, headers, payload):
        payloads.append(payload)
        return {"choices": [{"message": {"content": "test"}}]}
    monkeypatch.setattr(llm, "_post", post)
    client = llm.make_llm(chosen)
    monkeypatch.setenv("CAPTURE_LLM_MODEL", "later-model")
    assert client("observation") == "test"
    assert payloads[0]["model"] == "chosen-model"
    assert payloads[0]["temperature"] == 0.4
    assert "test-placeholder" not in json.dumps(chosen.metadata())


def test_strict_experiment_orchestration_offline(tmp_path, monkeypatch):
    """Test evidence gates using explicit test decisions and real env mutations."""
    from capture.llm import ModelConfig
    from env.generate import generate as actual_generate
    from loop import experiment
    from loop.agent import EpisodeResult
    from loop.sim_agent import SimAgent

    class TestAgent:
        agent_id = "browser:llm"
        metadata = {"provider": "injected-test", "model": "protocol-fixture"}
        def __init__(self, strategy, spec, **kwargs):
            self.spec, self.seen = spec, {}
            self.out = tmp_path / "baseline.json"
            self.headed = False
        async def run(self, env, task, session, skill_notes=""):
            if env is None:
                verdict = {"passed": bool(skill_notes), "reason": "test checker"}
                self.out.write_text(json.dumps({
                    "task": task["instruction"], "steps": [], "outcome": "failed",
                    "checker": verdict, "site_url": self.spec["site_url"]}))
                return EpisodeResult(verification=verdict, trajectory_path=str(self.out),
                                     agent_metadata=self.metadata)
            seed = session["seed"]
            self.seen[seed] = self.seen.get(seed, 0) + 1
            # Deterministic exploration fixture: a repeated training attempt succeeds.
            strategy = "smart" if skill_notes or (seed < 100 and self.seen[seed] > 1) else "naive"
            return await SimAgent(self.spec, strategy).run(env, task, session, skill_notes)

    monkeypatch.setattr(experiment, "BrowserAgent", TestAgent)
    prompts = []
    def provider(config):
        def call(prompt):
            prompts.append(prompt)
            assert '"seed": 100' not in prompt and '"seed": 101' not in prompt
            return "# Skills\n- paginate through observed pages before comparing."
        return call
    monkeypatch.setattr(experiment, "make_llm", provider)
    import env.generate
    monkeypatch.setattr(env.generate, "generate", lambda spec, out, **kw:
                        actual_generate(spec, out, llm=None))
    monkeypatch.delenv("LOOP_REAL_CHECKER", raising=False)
    args = experiment.parser().parse_args(["--out", str(tmp_path / "experiment"),
        "--rounds", "1", "--allow-verbatim"])
    args.train_seeds, args.eval_seeds, args.repro_seeds = [0, 1], [100, 101], [0, 1]
    report = asyncio.run(experiment.run(args, ModelConfig("injected-test", "fixture")))
    assert report["status"] == "complete"
    assert report["summary"]["improve"][0]["successes_distilled"] > 0
    assert prompts
    metrics = json.loads((tmp_path / "experiment" / "metrics.json").read_text())
    evals = [m for m in metrics["metrics"] if m["phase"] == "eval"]
    assert [m["round"] for m in evals] == [0, 1]
    assert evals[0]["evaluation_id"] == evals[1]["evaluation_id"]


@pytest.mark.skipif(os.environ.get("RUN_BROWSER_TESTS") != "1",
                    reason="requires Chromium")
def test_llm_action_interface_records_verifiable_browser_evidence(tmp_path, monkeypatch):
    """Injected decisions test tools/recordings, not model capability or learning."""
    from capture import agents
    from capture.llm import ModelConfig
    from env.runtime import running_env
    from loop.agent_browser import BrowserAgent
    from loop.runner import run_episode

    monkeypatch.setattr(agents, "_SETTLE_MS", 50)
    def decisions(prompt):
        state = json.loads(prompt.split("CURRENT STATE (JSON): ", 1)[1].split("\n\nPick ONE", 1)[0])
        current = state["current"]
        if current["can_add_to_cart"]:
            return '{"action":"add_to_cart"}'
        if not current["cards"]:
            return '{"action":"done"}'
        pages = [int(p) for p in current["pages"] if p.isdigit()]
        next_page = max([a["n"] for a in state["actions"] if a["action"] == "goto_page"] or [1]) + 1
        if next_page <= max(pages):
            return json.dumps({"action": "goto_page", "n": next_page})
        target = min(state["seen_products"], key=lambda p: float(p["price"].replace("$", "")))
        return json.dumps({"action": "open_product", "id": target["id"]})
    spec = config.load_spec("fixtures/env_spec.json")
    agent = BrowserAgent("llm", spec, llm_fn=decisions,
                         model_config=ModelConfig("injected-test", "test-decisions"),
                         artifact_dir=tmp_path / "episodes")
    with running_env("fixtures/env_spec.json", "fixtures/captured") as (url, app):
        result = asyncio.run(run_episode(
            EnvClient(url), agent, config.load_tasks(spec=spec)["shop-cheapest"],
            42, 0, overrides={"popup": 1, "labels": 3}))
    assert result["passed"], result
    assert result["agent_metadata"]["provider"] == "injected-test"
    trace = json.loads(open(result["trajectory_path"]).read())
    assert trace["outcome"] == "passed" and trace["checker"]["passed"]
    assert len(trace["steps"]) >= 5
    assert os.path.getsize(result["video_path"]) > 0
