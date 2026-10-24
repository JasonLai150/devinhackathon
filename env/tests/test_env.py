"""End-to-end checks of the env contract — the same paths C's runner will hit."""
import json
import os

import pytest

from env.app import create_app
from env import sessions


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ENV_SESSIONS_DIR", str(tmp_path / "sessions"))
    app = create_app()
    return app.test_client()


def reset(client, **params):
    r = client.post("/reset", query_string=params)
    assert r.status_code == 200
    return r.get_json()


def state(client, sid):
    r = client.get("/state", query_string={"session": sid})
    assert r.status_code == 200
    return r.get_json()


def verify(client, task_id, sid):
    return client.post("/verify", json={"task_id": task_id,
                                        "session_id": sid}).get_json()


def expected_flight(client, sid, **params):
    """Recompute the verifier's expected answer from the /state dump — the
    same way an honest agent (or C's tooling) would."""
    flights = state(client, sid)["tables"]["flights"]
    if params.get("direct_only"):
        flights = [f for f in flights if f["direct"] == 1]
    if params.get("departs_after"):
        flights = [f for f in flights if f["departs_at"] >= params["departs_after"]]
    sort_by = params.get("sort_by", "price")
    return sorted(flights, key=lambda f: (f[sort_by], f["price"], f["id"]))[0]


def book(client, sid, fid):
    return client.post(f"/s/{sid}/flights/{fid}/book",
                       data={"name": "Test Agent", "email": "a@b.co"})


def test_reset_and_state(client):
    r = reset(client, seed=42, popup=0, labels=0)
    sid, entry = r["session_id"], r["entry_url"]
    assert entry == f"/s/{sid}/"
    s = state(client, sid)
    assert len(s["tables"]["flights"]) >= 22
    assert s["meta"]["popup"] is False
    assert s["meta"]["labels"]["site_heading"] == "Flight Finder"


def test_seed_determinism(client):
    a = state(client, reset(client, seed=7)["session_id"])["tables"]["flights"]
    b = state(client, reset(client, seed=7)["session_id"])["tables"]["flights"]
    assert [f["flight_no"] for f in a] == [f["flight_no"] for f in b]


def test_happy_path_passes(client):
    sid = reset(client, seed=42, popup=0)["session_id"]
    assert client.get(f"/s/{sid}/").status_code == 200
    assert client.get(f"/s/{sid}/api/flights?page=1").get_json()["total_pages"] >= 3
    exp = expected_flight(client, sid, direct_only=True)
    assert client.get(f"/s/{sid}/flights/{exp['id']}").status_code == 200
    assert book(client, sid, exp["id"]).status_code == 302
    res = verify(client, "t2", sid)
    assert res["passed"], res["reason"]


def test_wrong_flight_fails(client):
    sid = reset(client, seed=42, popup=0)["session_id"]
    exp = expected_flight(client, sid)
    wrong = next(f for f in state(client, sid)["tables"]["flights"]
                 if f["id"] != exp["id"])
    client.get(f"/s/{sid}/flights/{wrong['id']}")
    book(client, sid, wrong["id"])
    res = verify(client, "t1", sid)
    assert not res["passed"] and "expected" in res["reason"]


def test_anticheat_rejects_direct_post(client):
    sid = reset(client, seed=42, popup=0)["session_id"]
    exp = expected_flight(client, sid)
    book(client, sid, exp["id"])  # skips the detail page entirely
    res = verify(client, "t1", sid)
    assert not res["passed"] and "shortcut" in res["reason"]


def test_no_booking_fails(client):
    sid = reset(client, seed=3, popup=0)["session_id"]
    res = verify(client, "t1", sid)
    assert not res["passed"] and "no booking" in res["reason"]


def test_sessions_isolated(client):
    s1 = reset(client, seed=1, popup=0)["session_id"]
    s2 = reset(client, seed=2, popup=0)["session_id"]
    exp1 = expected_flight(client, s1)
    client.get(f"/s/{s1}/flights/{exp1['id']}")
    book(client, s1, exp1["id"])
    assert verify(client, "t1", s1)["passed"]
    assert not verify(client, "t1", s2)["passed"]  # s2 untouched


def test_task_variants_diverge(client):
    """The structured distractors should make t1 and t2 expect different
    flights for every seed — the 'cheapest' trap."""
    for seed in range(10):
        sid = reset(client, seed=seed, popup=0)["session_id"]
        any_ = expected_flight(client, sid)
        direct = expected_flight(client, sid, direct_only=True)
        assert any_["direct"] == 0
        assert any_["price"] < direct["price"]


def test_popup_perturbation(client):
    sid = reset(client, seed=11, popup=1)["session_id"]
    assert b"No thanks" in client.get(f"/s/{sid}/").data
    sid2 = reset(client, seed=11, popup=0)["session_id"]
    assert b"No thanks" not in client.get(f"/s/{sid2}/").data
