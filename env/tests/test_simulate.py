"""Simulator regression: naive (page-1-only) must fail where the target is
deeper; smart (paginating) must pass every seed. This is the failure-mode
reproduction in miniature."""
import pytest

from env import sessions
from env.app import create_app
from env.simulate_agent import run_episode
from env.verifiers import load_tasks


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ENV_SESSIONS_DIR", str(tmp_path / "sessions"))
    return create_app().test_client()


@pytest.fixture()
def spec():
    return create_app().config["SPEC"]


def test_smart_passes_all_seeds(client, spec):
    task = load_tasks()["t2"]
    for seed in range(10):
        res = run_episode(client, spec, task, seed, "smart")
        assert res["passed"], f"seed {seed}: {res['reason']}"


def test_naive_fails_when_target_is_deep(client, spec):
    """Naive only fails when the constrained argmin isn't on page 1 — verify
    the strategy diverges from smart on at least one seed and fails then."""
    task = load_tasks()["t2"]
    failures = [s for s in range(10)
                if not run_episode(client, spec, task, s, "naive")["passed"]]
    assert len(failures) >= 4  # most seeds hide the target past page 1
    for s in failures:
        res = run_episode(client, spec, task, s, "naive")
        assert "expected" in res["reason"]
