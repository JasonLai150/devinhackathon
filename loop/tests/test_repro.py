"""repro_check: signature classification + verdicts."""
import asyncio

import pytest

from env.app import create_app
from loop import config, repro_check
from loop.env_client import EnvClient
from loop.sim_agent import SimAgent


@pytest.fixture()
def factory(tmp_path, monkeypatch):
    monkeypatch.setenv("ENV_SESSIONS_DIR", str(tmp_path / "sessions"))
    app = create_app()
    return lambda: EnvClient.for_app(app)


@pytest.fixture()
def spec():
    return config.load_spec()


@pytest.fixture()
def task():
    return config.load_tasks()["t2"]


def run(factory, strategy, task, spec, seeds=range(10)):
    agent = SimAgent(spec, strategy)
    return asyncio.run(repro_check.repro(factory, agent, task, seeds, spec))


def test_naive_reproduces(factory, task, spec):
    episodes = run(factory, "naive", task, spec)
    report = repro_check.build_report("sim:naive", task, spec, episodes, None)
    assert report["sandbox_reproduced"] is True
    assert report["reproduced"] is False
    assert report["failures"] > 0
    assert report["signature_failures"] == report["failures"]


def test_smart_does_not_reproduce(factory, task, spec):
    episodes = run(factory, "smart", task, spec)
    report = repro_check.build_report("sim:smart", task, spec, episodes, None)
    assert report["reproduced"] is False
    assert report["failures"] == 0


def test_signature_reads_request_log(factory, task, spec):
    """A failed naive episode must show max page 1 + a page-1 booking."""
    episodes = run(factory, "naive", task, spec, seeds=range(10))
    sig = [e for e in episodes if e["signature_failure"]]
    assert sig
    for e in sig:
        assert e["max_listing_page_seen"] <= 1 or e["booked_from_page1"]


def test_passing_episode_is_not_a_signature_failure(factory, task, spec):
    episodes = run(factory, "naive", task, spec, seeds=range(10))
    for e in episodes:
        if e["passed"]:
            assert e["signature_failure"] is False


def test_infrastructure_error_is_not_pagination_repro(factory, task, spec):
    client = factory()
    session = client.reset(seed=42)
    state = client.get_state(session["session_id"])
    result = repro_check.classify(state, spec, {"passed": False, "reason": "error: broken JS"})
    assert result["signature_failure"] is False


def test_unrelated_cart_failure_is_not_pagination():
    shop = config.load_spec("fixtures/env_spec.json")
    state = {"tables": {
        "products": [{"id": 1}, {"id": 2}],
        "cart_items": [{"product_id": 1, "quantity": 2}],
        "request_log": [{"path": "/s/example/api/products?page=1"}]}}
    result = repro_check.classify(state, shop, {
        "passed": False, "reason": "expected exactly one item with quantity 1"})
    assert result["signature_failure"] is False


def test_missing_or_unrelated_real_evidence_cannot_confirm_reproduction(task, spec):
    episodes = [{"passed": False, "signature_failure": True}]
    for trajectory in (None, {"outcome": "failed", "failure_step": 2,
                             "steps": [], "checker": {"passed": False}}):
        report = repro_check.build_report("browser:llm", task, spec, episodes, trajectory)
        assert report["reproduced"] is False


def test_real_and_sandbox_failure_causes_must_match():
    shop = config.load_spec("fixtures/env_spec.json")
    trace = {
        "site_url": shop["site_url"], "outcome": "failed",
        "checker": {"passed": False, "code": "wrong_choice",
                    "cart": [{"name": "Pliers", "price": 9, "quantity": 1}],
                    "expected": {"name": "Washers", "price": 3}},
        "steps": [{"listing": {"page": 1, "products": [{"name": "Pliers", "price": 9}]}}]}
    episode = {"passed": False, "signature_failure": True}
    report = repro_check.build_report("browser:auto", {"id": "shop-cheapest"}, shop, [episode], trace)
    assert report["reproduced"]
    trace["steps"].append({"listing": {"page": 2, "products": [{"name": "Washers", "price": 3}]}})
    assert not repro_check.build_report(
        "browser:auto", {"id": "shop-cheapest"}, shop, [episode], trace)["reproduced"]
    trace["steps"].pop()
    trace["site_url"] = "https://another-site.test"
    assert not repro_check.build_report(
        "browser:auto", {"id": "shop-cheapest"}, shop, [episode], trace)["reproduced"]
    trace["checker"]["cart"][0]["quantity"] = 2
    assert repro_check.real_failure(trace)["failure_kind"] is None
