"""EnvClient contract round-trip against B's app, in-process."""
import pytest

from env.app import create_app
from loop.env_client import EnvClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ENV_SESSIONS_DIR", str(tmp_path / "sessions"))
    return EnvClient.for_app(create_app())


def test_reset_returns_session_and_entry(client):
    r = client.reset(seed=42, popup=0, labels=0)
    assert r["seed"] == 42
    assert r["entry_url"] == f"/s/{r['session_id']}/"


def test_browse_entry_page(client):
    sid = client.reset(seed=1, popup=0)["session_id"]
    status, text = client.get(f"/s/{sid}/")
    assert status == 200 and "<html" in text.lower()


def test_paginated_stub_api(client):
    sid = client.reset(seed=1, popup=0)["session_id"]
    page = client.get_json(f"/s/{sid}/api/flights", params={"page": 1})
    assert page["total_pages"] >= 2
    assert len(page["items"]) == page["per_page"]


def test_get_state_dumps_tables(client):
    sid = client.reset(seed=1, popup=0)["session_id"]
    state = client.get_state(sid)
    assert state["tables"]["flights"]
    assert state["meta"]["seed"] == 1


def test_verify_round_trip(client):
    sid = client.reset(seed=1, popup=0)["session_id"]
    res = client.verify("t1", sid)
    assert res["passed"] is False
    assert "no booking" in res["reason"]


def test_list_sessions(client):
    sid = client.reset(seed=1, popup=0)["session_id"]
    assert any(s["session_id"] == sid for s in client.list_sessions())


def test_form_post_returns_status(client):
    sid = client.reset(seed=1, popup=0)["session_id"]
    fid = client.get_state(sid)["tables"]["flights"][0]["id"]
    client.get(f"/s/{sid}/flights/{fid}")
    status = client.post_form(f"/s/{sid}/flights/{fid}/book",
                              {"name": "T", "email": "t@t.co"})
    assert status == 302
