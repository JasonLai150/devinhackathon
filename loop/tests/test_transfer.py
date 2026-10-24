"""transfer: stub checker honesty, checker delegation, rehearse path."""
import asyncio
import json

import pytest

from env.app import create_app
from loop import config, real_site_checker, transfer
from loop.env_client import EnvClient
from loop.sim_agent import SimAgent


@pytest.fixture()
def task():
    return config.load_tasks()["t2"]


@pytest.fixture()
def spec():
    return config.load_spec()


def test_stub_checker_never_fakes_green(task, monkeypatch):
    monkeypatch.delenv("LOOP_REAL_CHECKER", raising=False)
    v = real_site_checker.check_transfer(task, "https://x", {})
    assert v["passed"] is False and "not integrated" in v["reason"]


def test_checker_delegates_via_env_var(task, monkeypatch, tmp_path):
    mod = tmp_path / "fake_checker.py"
    mod.write_text(
        "def check(task, url, outcome):\n"
        "    return {'passed': True, 'reason': 'scripted ok'}\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("LOOP_REAL_CHECKER", "fake_checker:check")
    v = real_site_checker.check_transfer(task, "https://x", {})
    assert v == {"passed": True, "reason": "scripted ok"}


def test_rehearse_transfer_passes_with_smart(spec, task, tmp_path,
                                             monkeypatch):
    monkeypatch.setenv("ENV_SESSIONS_DIR", str(tmp_path / "sessions"))
    env = EnvClient.for_app(create_app())
    session = env.reset()
    sid = session["session_id"]
    agent = SimAgent(spec, "smart")

    def checker(task_, url, outcome):
        return env.verify(task_["id"], sid)

    rec = asyncio.run(transfer.transfer(
        agent, task, session["entry_url"], checker=checker, env=env,
        session_id=sid))
    assert rec["passed"] is True, rec["reason"]
    assert rec["phase"] == "transfer"


def test_agent_error_is_failed_transfer(spec, task):
    from loop.agent import Agent

    class Dead(Agent):
        agent_id = "dead"

        async def run(self, env, task, session, skill_notes=""):
            raise RuntimeError("no harness")

    rec = asyncio.run(transfer.transfer(Dead(), task, "https://x"))
    assert rec["passed"] is False and "not integrated" in rec["reason"]


def test_real_checker_requires_one_verified_cart_item(monkeypatch):
    from capture import checker
    expected = {"id": "washers", "name": "Washers", "price": 3.55}
    rows = [{"name": "Washers", "price": 3.55, "quantity": 2}]
    monkeypatch.setattr(checker, "cart_contents", lambda page: rows)
    assert not checker.verify(None, expected)["passed"]
    rows[0]["quantity"] = 1
    assert checker.verify(None, expected)["passed"]
    rows[0]["price"] = None
    assert not checker.verify(None, expected)["passed"]


@pytest.mark.parametrize("provider,model,temperature,key", [
    ("openai", "saved-model", 0.2, "OPENAI_API_KEY"),
    ("gemini", "gemini-3.8-flash", None, "GEMINI_API_KEY"),
])
def test_saved_agent_restores_settings_and_checks_memory(tmp_path, monkeypatch,
                                                         provider, model, temperature, key):
    from capture.llm import ModelConfig
    from loop.agent_browser import BrowserAgent
    from loop.improve import write_agent_config
    from loop.memory import MemoryStore

    monkeypatch.setenv(key, "test-placeholder")
    monkeypatch.setenv("CAPTURE_LLM_MODEL", "different-model")
    spec = config.load_spec("fixtures/env_spec.json")
    task = config.load_tasks(spec=spec)["shop-cheapest"]
    agent = BrowserAgent("llm", spec, model_config=ModelConfig(provider, model, temperature),
                         llm_fn=lambda prompt: "unused", max_steps=17)
    memory = MemoryStore(str(tmp_path / "notes.md"))
    memory.save("Compare every page.")
    path = tmp_path / "agent.json"
    write_agent_config(str(path), agent, task, memory, [], [0], [100], {})
    _, _, _, restored, notes = transfer.load_saved_agent(path)
    assert restored.metadata == agent.metadata
    assert notes == "Compare every page."
    cfg = json.loads(path.read_text())
    assert cfg["memory_path"] == "notes.md"
    import shutil
    copied = tmp_path / "portable"
    copied.mkdir()
    for name in ("agent.json", cfg["spec_path"], cfg["memory_path"]):
        shutil.copy2(tmp_path / name, copied / name)
    assert transfer.load_saved_agent(copied / "agent.json")[4] == notes
    changed = dict(cfg)
    changed["agent_metadata"] = {**cfg["agent_metadata"], "policy_version": "old-policy"}
    (copied / "agent.json").write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="browser policy"):
        transfer.load_saved_agent(copied / "agent.json")
    (copied / "agent.json").write_text(json.dumps(cfg))
    (copied / cfg["spec_path"]).write_text("{}")
    with pytest.raises(ValueError, match="spec hash"):
        transfer.load_saved_agent(copied / "agent.json")
    memory.save("changed")
    with pytest.raises(ValueError, match="memory hash"):
        transfer.load_saved_agent(path)
    (tmp_path / "notes.md").unlink()
    with pytest.raises(FileNotFoundError):
        transfer.load_saved_agent(path)


def test_checker_exception_produces_failed_transfer(task):
    from loop.agent import Agent, EpisodeResult
    class Complete(Agent):
        async def run(self, *args, **kwargs):
            return EpisodeResult()
    def broken(*args):
        raise RuntimeError("checker unavailable")
    rec = asyncio.run(transfer.transfer(Complete(), task, "https://example.test", checker=broken))
    assert not rec["passed"] and "checker unavailable" in rec["reason"]


def test_missing_explicit_config_cannot_start_transfer(tmp_path):
    with pytest.raises(FileNotFoundError):
        transfer.main(["--config", str(tmp_path / "missing.json")])


def test_failed_agent_cannot_pass_a_successful_checker(task):
    from loop.agent import Agent, EpisodeResult
    class Failed(Agent):
        async def run(self, *args, **kwargs):
            return EpisodeResult(ok=False)
    rec = asyncio.run(transfer.transfer(
        Failed(), task, "https://example.test",
        checker=lambda *args: {"passed": True, "reason": "cart exists"}))
    assert not rec["passed"] and "agent execution failed" in rec["reason"]
