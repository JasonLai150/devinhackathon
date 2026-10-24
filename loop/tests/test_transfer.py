"""transfer: stub checker honesty, checker delegation, rehearse path."""
import asyncio

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
