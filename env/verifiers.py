"""Verifiers: check session-DB state, never screenshots.

verify(task_id, sid, spec) loads the task from tasks.json, dispatches to the
named verifier function, and returns {passed, reason}. Expected answers are
always computed from the session's live seeded data — perturbations can't
stale them.

Anti-cheat: every env request under /s/<sid>/ is written to request_log, so
verifiers can require real navigation (e.g., the booked entity's detail page
must have been visited before the mutation POST).
"""
import json
import os
import re

from . import sessions

_TASKS_PATH = os.path.join(os.path.dirname(__file__), "tasks.json")
_DEFAULT_SPEC = os.path.join(os.path.dirname(__file__), "spec", "env_spec.json")

VERIFIERS = {}


def verifier(name):
    def deco(fn):
        VERIFIERS[name] = fn
        return fn
    return deco


def load_tasks():
    with open(_TASKS_PATH) as f:
        return {t["id"]: t for t in json.load(f)["tasks"]}


def load_default_spec():
    with open(_DEFAULT_SPEC) as f:
        return json.load(f)


def verify(task_id, sid, spec=None):
    spec = spec or load_default_spec()
    conn = sessions.connect(sid)
    if conn is None:
        return {"passed": False, "reason": f"unknown session {sid}"}
    tasks = load_tasks()
    if task_id not in tasks:
        conn.close()
        return {"passed": False, "reason": f"unknown task {task_id}"}
    task = tasks[task_id]
    fn = VERIFIERS.get(task["verifier"])
    if fn is None:
        conn.close()
        return {"passed": False, "reason": f"no verifier named {task['verifier']}"}
    try:
        return fn(conn, task.get("params", {}), sid, spec)
    finally:
        conn.close()


def detail_visit_path(spec, entity_id):
    """'/flights/<int:eid>' + 24 -> 'flights/24' — the suffix request_log must
    contain for a visit to count."""
    path = spec["routes"]["detail"]["path"]
    return re.sub(r"<int:\w+>", str(entity_id), path).lstrip("/")


def _visited(conn, sid, suffix):
    row = conn.execute(
        "SELECT 1 FROM request_log WHERE method='GET' AND path = ? LIMIT 1",
        (f"/s/{sid}/{suffix}",),
    ).fetchone()
    return row is not None


@verifier("booked_matching_flight")
def booked_matching_flight(conn, params, sid, spec):
    """Expected = argmin over flights matching params; bookings must contain it.

    params: direct_only (bool), departs_after ("HH:MM"), sort_by
    ("price" default | "departs_at"), order ("asc" default | "desc").
    """
    where, args = [], []
    if params.get("direct_only"):
        where.append("direct = 1")
    if params.get("departs_after"):
        where.append("departs_at >= ?")
        args.append(params["departs_after"])
    sql = "SELECT * FROM flights"
    if where:
        sql += " WHERE " + " AND ".join(where)
    rows = conn.execute(sql, args).fetchall()
    if not rows:
        return {"passed": False, "reason": "no flights match task constraints"}

    sort_by = params.get("sort_by", "price")
    expected = sorted(rows, key=lambda r: (r[sort_by], r["price"], r["id"]),
                      reverse=params.get("order") == "desc")[0]

    booking = conn.execute(
        "SELECT * FROM bookings ORDER BY id DESC LIMIT 1").fetchone()
    if booking is None:
        return {"passed": False, "reason": "no booking was made"}

    if not _visited(conn, sid, detail_visit_path(spec, booking["flight_id"])):
        return {"passed": False,
                "reason": "booking made without visiting the flight page — "
                          "suspected shortcut"}

    if booking["flight_id"] != expected["id"]:
        actual = conn.execute("SELECT * FROM flights WHERE id = ?",
                              (booking["flight_id"],)).fetchone()
        return {"passed": False,
                "reason": f"booked {actual['flight_no']} (${actual['price']}), "
                          f"expected {expected['flight_no']} (${expected['price']})"}
    return {"passed": True,
            "reason": f"booked {expected['flight_no']} at ${expected['price']}"}
