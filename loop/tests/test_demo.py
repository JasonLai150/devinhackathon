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

    assert summary["repro"]["sandbox_reproduced"] is True
    assert summary["repro"]["reproduced"] is False
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
    with pytest.raises(ValueError, match="fresh artifacts"):
        asyncio.run(demo.run(factory, spec, task, rounds=1,
                             train_seeds=[0], eval_seeds=[100], repro_seeds=[0]))


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
    assert summary["repro"]["sandbox_reproduced"]
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
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY",
                 "GOOGLE_API_KEY", "CAPTURE_LLM_PROVIDER"):
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


def test_gemini_wire_protocol_and_saved_model(monkeypatch):
    from capture import llm
    monkeypatch.setenv("GEMINI_API_KEY", "test-placeholder")
    chosen = llm.resolve_config("gemini", "gemini-3.8-flash", 0.4)
    requests = []
    def post(url, headers, payload):
        requests.append((url, headers, payload))
        return {"candidates": [{"finishReason": "STOP", "content": {"parts": [
            {"text": "internal", "thought": True}, {"text": '{"action":"done"}'}]}}]}
    monkeypatch.setattr(llm, "_post", post)
    monkeypatch.setenv("CAPTURE_LLM_MODEL", "changed-later")
    assert llm.make_llm(chosen)("observation") == '{"action":"done"}'
    url, headers, payload = requests[0]
    assert url.endswith("/models/gemini-3.8-flash:generateContent")
    assert headers["x-goog-api-key"] == "test-placeholder"
    assert "test-placeholder" not in url
    assert payload["contents"][0]["parts"] == [{"text": "observation"}]
    assert chosen.temperature is None and payload["generationConfig"] == {}
    assert "test-placeholder" not in json.dumps(chosen.metadata())
    monkeypatch.setattr(llm, "_post", lambda *args: {"candidates": []})
    with pytest.raises(RuntimeError, match="no candidate"):
        llm.make_llm(chosen)("observation")
    monkeypatch.setattr(llm, "_post", lambda *args: {"candidates": [{"finishReason": "MAX_TOKENS"}]})
    with pytest.raises(RuntimeError, match="incomplete"):
        llm.make_llm(chosen)("observation")


def test_local_credentials_load_without_shell_evaluation(tmp_path, monkeypatch):
    from capture.llm import load_env_file
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("CAPTURE_LLM_MODEL", "existing-model")
    path = tmp_path / ".env.local"
    path.write_text('# local settings\nGEMINI_API_KEY="literal-$(not-executed)"\n'
                    'CAPTURE_LLM_MODEL=ignored-model\n')
    load_env_file(path)
    assert os.environ["GEMINI_API_KEY"] == "literal-$(not-executed)"
    assert os.environ["CAPTURE_LLM_MODEL"] == "existing-model"
    path.write_text("unsupported-secret-line")
    with pytest.raises(ValueError, match="line 1") as exc:
        load_env_file(path)
    assert "unsupported-secret-line" not in str(exc.value)


def test_dashboard_serves_artifacts_but_not_credentials(tmp_path):
    import functools
    import http.server
    import urllib.request
    import urllib.error
    from dashboard.serve import ArtifactHandler
    (tmp_path / ".env.local").write_text("GEMINI_API_KEY=test-placeholder")
    (tmp_path / ".integration").mkdir()
    (tmp_path / ".integration" / "result.json").write_text('{"passed":true}')
    (tmp_path / "dashboard").mkdir()
    (tmp_path / "dashboard" / "index.html").write_text("dashboard")
    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(ArtifactHandler, directory=str(tmp_path)))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        with urllib.request.urlopen(base) as response:
            assert response.read() == b"dashboard"
        with urllib.request.urlopen(base + "/.integration/result.json") as response:
            assert json.load(response) == {"passed": True}
        for path in ("/.env.local", "/%2eenv.local", "/.git/config", "/.integration/"):
            with pytest.raises(urllib.error.HTTPError) as exc:
                urllib.request.urlopen(base + path)
            assert exc.value.code == 404
    finally:
        server.shutdown()
        server.server_close()
        worker.join()

def test_provider_http_error_preserves_details_and_redacts_credentials(monkeypatch):
    import io
    import urllib.error
    from capture import llm
    secret = "test-secret-not-for-output"
    monkeypatch.setenv("ANTHROPIC_API_KEY", secret)
    body = json.dumps({"error": {"type": "invalid_request_error",
                               "message": f"Credit balance too low; credential {secret}"}}).encode()
    def reject(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 400, "Bad Request",
                                     {"request-id": "req-test"}, io.BytesIO(body))
    monkeypatch.setattr(llm.urllib.request, "urlopen", reject)
    with pytest.raises(llm.ProviderError) as exc:
        llm.make_llm(llm.ModelConfig("anthropic", "test-model"))("observation")
    message = str(exc.value)
    assert "HTTP 400" in message and "invalid_request_error" in message
    assert "Credit balance too low" in message and "req-test" in message
    assert "[REDACTED]" in message and secret not in message


def test_provider_non_json_error_does_not_dump_response_body(monkeypatch):
    import io
    import urllib.error
    from capture import llm
    def reject(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 502, "Bad Gateway", {},
                                     io.BytesIO(b"<html>untrusted proxy debug output</html>"))
    monkeypatch.setattr(llm.urllib.request, "urlopen", reject)
    with pytest.raises(llm.ProviderError) as exc:
        llm._post("https://api.anthropic.com/v1/messages", {}, {})
    assert "HTTP 502" in str(exc.value)
    assert "untrusted" not in str(exc.value)


def test_provider_probe_checks_configured_model_with_small_output_limit(monkeypatch):
    from capture import llm
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-placeholder")
    payloads = []
    def post(url, headers, payload):
        payloads.append(payload)
        return {"content": [{"type": "text", "text": "OK"}]}
    monkeypatch.setattr(llm, "_post", post)
    result = llm.check_provider(llm.ModelConfig("anthropic", "chosen-model", 0.7))
    assert result["ok"]
    assert payloads[0]["model"] == "chosen-model"
    assert payloads[0]["temperature"] == 0.7
    assert payloads[0]["max_tokens"] == 16


def test_gemini_request_and_text_response(monkeypatch):
    from capture import llm
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "CAPTURE_LLM_PROVIDER"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "gemini-test-placeholder")
    requests = []
    def post(url, headers, payload):
        requests.append((url, headers, payload))
        return {"candidates": [{"finishReason": "STOP", "content": {"parts": [
            {"thought": True, "text": "not answer text"},
            {"text": '{"action":'}, {"text": '"done"}'}]}}]}
    monkeypatch.setattr(llm, "_post", post)
    chosen = llm.resolve_config(model="models/gemini-3.5-flash-lite", temperature=0.7)
    assert chosen.provider == "gemini"
    result = llm.make_llm(chosen)("browser observation")
    assert json.loads(result) == {"action": "done"}
    url, headers, payload = requests[0]
    assert url == "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent"
    assert "gemini-test-placeholder" not in url
    assert headers["x-goog-api-key"] == "gemini-test-placeholder"
    assert payload["contents"][0]["parts"] == [{"text": "browser observation"}]
    assert payload["generationConfig"]["temperature"] == 0.7
    llm.check_provider(chosen)
    assert requests[-1][2]["generationConfig"]["maxOutputTokens"] == 256


@pytest.mark.parametrize("response,reason", [
    ({"promptFeedback": {"blockReason": "SAFETY"}}, "SAFETY"),
    ({"candidates": [{"finishReason": "MAX_TOKENS"}]}, "MAX_TOKENS"),
    ({"candidates": [{"finishReason": "STOP", "content": {"parts": []}}]}, "no answer text"),
])
def test_gemini_missing_or_truncated_answers_are_errors(monkeypatch, response, reason):
    from capture import llm
    monkeypatch.setenv("GEMINI_API_KEY", "test-placeholder")
    monkeypatch.setattr(llm, "_post", lambda *args: response)
    with pytest.raises(llm.ProviderError, match=reason):
        llm.make_llm(llm.ModelConfig("gemini", "test-model"))("observation")


def test_gemini_error_status_and_key_are_handled_safely(monkeypatch):
    import io
    import urllib.error
    from capture import llm
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test-secret")
    def reject(request, timeout):
        body = json.dumps({"error": {"status": "PERMISSION_DENIED",
                           "message": "key gemini-test-secret cannot access this model"}}).encode()
        raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, io.BytesIO(body))
    monkeypatch.setattr(llm.urllib.request, "urlopen", reject)
    with pytest.raises(llm.ProviderError) as error:
        llm.make_llm(llm.ModelConfig("gemini", "test-model"))("observation")
    assert "PERMISSION_DENIED" in str(error.value)
    assert "gemini-test-secret" not in str(error.value)
    assert "[REDACTED]" in str(error.value)


def test_gemini_pacing_is_shared_and_bounded(monkeypatch):
    from capture import llm
    clock, sleeps = [100.0], []
    monkeypatch.setenv("GEMINI_RPM", "10")
    monkeypatch.setattr(llm, "_GEMINI_NEXT_REQUEST", 0.0)
    monkeypatch.setattr(llm.time, "monotonic", lambda: clock[0])
    def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds
    monkeypatch.setattr(llm.time, "sleep", sleep)
    for _ in range(3):
        llm._pace_gemini()
    assert sleeps == [6.0, 6.0]
    monkeypatch.setenv("GEMINI_RPM", "0")
    with pytest.raises(ValueError):
        llm._pace_gemini()


def test_gemini_retries_only_with_bounded_server_delay(monkeypatch):
    from capture import llm
    calls, sleeps = [], []
    def post(*args):
        calls.append(args)
        if len(calls) == 1:
            raise llm.ProviderError("temporary quota", status=429, retry_after=12.5)
        return {"ok": True}
    monkeypatch.setattr(llm, "_post_once", post)
    monkeypatch.setattr(llm, "_pace_gemini", lambda: None)
    monkeypatch.setattr(llm.time, "sleep", sleeps.append)
    assert llm._post("https://generativelanguage.googleapis.com/v1beta/models/test:generateContent", {}, {})["ok"]
    assert len(calls) == 2 and sleeps == [13.0]


@pytest.mark.parametrize("execution_error,expected_status,attempts", [
    ("HTTP 400: invalid_request_error", "error", 1),
    (None, "blocked", 3),
])
def test_baseline_execution_errors_are_not_task_outcomes(
        tmp_path, monkeypatch, execution_error, expected_status, attempts):
    from capture.llm import ModelConfig
    from loop import experiment
    from loop.agent import EpisodeResult

    class BaselineAgent:
        agent_id = "browser:llm"
        metadata = {"provider": "injected-test", "model": "baseline-fixture"}
        def __init__(self, *args, **kwargs):
            pass
        async def run(self, *args, **kwargs):
            return EpisodeResult(ok=execution_error is None, error=execution_error,
                                 verification=None if execution_error else
                                 {"passed": True, "reason": "test success"})
    monkeypatch.setattr(experiment, "BrowserAgent", BaselineAgent)
    args = experiment.parser().parse_args(["--out", str(tmp_path / "experiment")])
    result = asyncio.run(experiment.run(args, ModelConfig("anthropic", "fixture")))
    assert result["status"] == expected_status
    assert result["baseline"]["n"] == attempts
    assert result["baseline"]["task_failures"] == 0
    if execution_error:
        assert "Baseline execution failed" in result["reason"]
        assert "different candidate" not in result["reason"]
        assert result["baseline"]["execution_errors"] == 1


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
                verdict = {"passed": bool(skill_notes), "reason": "test checker",
                           "code": "wrong_choice",
                           "expected": {"name": "Washers", "price": 3},
                           "cart": [{"name": "Pliers", "price": 9, "quantity": 1}]}
                self.out.write_text(json.dumps({
                    "task": task["instruction"],
                    "steps": [{"listing": {"page": 1, "products": [{"name": "Pliers", "price": 9}]}}],
                    "outcome": "passed" if verdict["passed"] else "failed",
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
    from types import SimpleNamespace
    from loop import continue_experiment
    monkeypatch.setattr(continue_experiment, "BrowserAgent", TestAgent)
    monkeypatch.setattr(continue_experiment, "make_llm", provider)
    continued = asyncio.run(continue_experiment.run(SimpleNamespace(
        directory=args.out, rounds=1, attempts=1, timeout=120,
        concurrency=1, headed=False, explore=False), ModelConfig("injected-test", "fixture")))
    assert continued["status"] == "complete"
    metrics = json.loads((tmp_path / "experiment" / "metrics.json").read_text())
    evals = [m for m in metrics["metrics"] if m["phase"] == "eval"]
    assert [m["round"] for m in evals] == [0, 1, 2]
    assert len({m["evaluation_id"] for m in evals}) == 1
    assert len(json.loads((tmp_path / "experiment" / "transfer_history.json").read_text())) == 1


def test_cancelled_experiment_retains_an_interrupted_manifest(tmp_path, monkeypatch):
    from capture.llm import ModelConfig
    from loop import experiment
    class CancelledAgent:
        agent_id = "browser:llm"
        metadata = {"provider": "injected-test"}
        def __init__(self, *args, **kwargs):
            pass
        async def run(self, *args, **kwargs):
            raise asyncio.CancelledError
    monkeypatch.setattr(experiment, "BrowserAgent", CancelledAgent)
    args = experiment.parser().parse_args(["--out", str(tmp_path / "cancelled")])
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(experiment.run(args, ModelConfig("injected-test", "fixture")))
    report = json.loads((tmp_path / "cancelled" / "run_manifest.json").read_text())
    assert report["status"] == "interrupted" and report["env_running"] is False


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
    calls = []
    def decisions(prompt):
        state = json.loads(prompt.split("CURRENT STATE (JSON): ", 1)[1].split("\n\nPick ONE", 1)[0])
        calls.append(state)
        if len(calls) == 1:
            return '{"action":"goto_page","n":999}'
        if len(calls) == 2:
            assert state["actions"][-1]["action"] == "invalid"
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
        from capture.llm import ProviderError
        def unavailable(prompt):
            raise ProviderError("API quota exhausted", status=429)
        broken = BrowserAgent("llm", spec, llm_fn=unavailable)
        failed = asyncio.run(run_episode(
            EnvClient(url), broken, config.load_tasks(spec=spec)["shop-cheapest"], 43, 0))
        assert not failed["passed"] and failed["error_kind"] == "provider"
    assert result["passed"], result
    assert result["agent_metadata"]["provider"] == "injected-test"
    trace = json.loads(open(result["trajectory_path"]).read())
    assert trace["outcome"] == "passed" and trace["checker"]["passed"]
    assert len(trace["steps"]) >= 5
    assert trace["observations"] and result["observations"]
    assert os.path.getsize(result["video_path"]) > 0


@pytest.mark.skipif(os.environ.get("RUN_BROWSER_TESTS") != "1",
                    reason="requires Chromium")
def test_observation_uses_visible_page_numbers_on_real_and_sandbox_markup():
    from playwright.sync_api import sync_playwright
    from capture.agents import _OBSERVE_JS
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content('''<ul class="pagination">
          <li><button class="page-link" aria-label="Previous">«</button></li>
          <li><button class="page-link" aria-label="Page-1">1</button></li>
          <li><button class="page-link" aria-label="Page-2">2</button></li>
          <li><button class="page-link" aria-label="Next">»</button></li></ul>''')
        assert page.evaluate(_OBSERVE_JS)["pages"] == ["Previous", "1", "2", "Next"]
        browser.close()
