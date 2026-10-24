"""Failure-to-Env mini-env: deterministic Flask skeleton + component slot.

Endpoints (contract with C):
  POST /reset?seed=N[&popup=0|1&labels=i]  -> {session_id, entry_url}
  GET  /s/<sid>/...                        -> the env; agent browses here
  GET  /state?session=<sid>                -> full DB dump + meta
  POST /verify {task_id, session_id}       -> {passed, reason}
  GET  /sessions                           -> session list (dashboard/debug)

Domain-specific routes below (/flights/..., booking POST) are the dev-fixture
wiring; the generator will own route generation from spec.pages later.
"""
import json
import os
import random
import time

from flask import Flask, abort, jsonify, redirect, request, send_file
from flask.templating import render_template
from jinja2 import ChoiceLoader, FileSystemLoader

from . import sessions, stubs, verifiers

_BASE = os.path.dirname(__file__)
_DEFAULT_SPEC = os.path.join(_BASE, "spec", "env_spec.json")


def create_app(spec_path=None):
    app = Flask(__name__)
    spec_path = spec_path or os.environ.get("ENV_SPEC", _DEFAULT_SPEC)
    with open(spec_path) as f:
        app.config["SPEC"] = json.load(f)

    # Component slot: component/ is a second template-search path so the
    # generator drops component.html/js there without touching templates/.
    app.jinja_loader = ChoiceLoader([
        FileSystemLoader(os.path.join(_BASE, "templates")),
        FileSystemLoader(os.path.join(_BASE, "component")),
    ])

    stubs.register_stubs(app, app.config["SPEC"].get("backend_calls", []))

    def _conn_or_404(sid):
        conn = sessions.connect(sid)
        if conn is None:
            abort(404)
        return conn

    def _page_ctx(conn, sid):
        return {"sid": sid, "meta": sessions.read_meta(conn)}

    @app.before_request
    def _log_session_requests():
        # Anti-cheat + trajectory signal: record all env-page traffic.
        p = request.path
        if p.startswith("/s/"):
            sid = p.split("/")[2]
            conn = sessions.connect(sid)
            if conn is not None:
                try:
                    conn.execute(
                        "INSERT INTO request_log (ts, method, path) VALUES (?,?,?)",
                        (time.time(), request.method, p))
                    conn.commit()
                finally:
                    conn.close()

    @app.get("/")
    def root():
        return jsonify({"service": "failure-to-env", "spec": app.config["SPEC"].get("name")})

    @app.post("/reset")
    def reset():
        seed = request.args.get("seed", type=int)
        if seed is None:
            seed = random.randint(0, 2**31 - 1)
        overrides = {k: request.args[k] for k in ("popup", "labels")
                     if k in request.args}
        sid = sessions.create_session(seed, app.config["SPEC"], overrides)
        return jsonify({"session_id": sid, "entry_url": f"/s/{sid}/", "seed": seed})

    @app.get("/state")
    def state():
        sid = request.args.get("session", "")
        conn = _conn_or_404(sid)
        try:
            tables = {}
            for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"):
                name = r["name"]
                rows = conn.execute(f"SELECT * FROM {name}").fetchall()
                tables[name] = [dict(x) for x in rows]
            return jsonify({"session_id": sid, "tables": tables,
                            "meta": sessions.read_meta(conn)})
        finally:
            conn.close()

    @app.get("/sessions")
    def session_list():
        return jsonify({"sessions": sessions.list_sessions()})

    @app.post("/verify")
    def verify():
        body = request.get_json(force=True) or {}
        return jsonify(verifiers.verify(body.get("task_id", ""),
                                        body.get("session_id", "")))

    # ---- dev-fixture routes (generator will own these from spec.pages) ----

    @app.get("/s/<sid>/")
    def index(sid):
        conn = _conn_or_404(sid)
        try:
            meta = sessions.read_meta(conn)
            return render_template("index.html", labels=meta["labels"],
                                   **_page_ctx(conn, sid))
        finally:
            conn.close()

    @app.get("/s/<sid>/component.js")
    def component_js(sid):
        if not sessions.exists(sid):
            abort(404)
        return send_file(os.path.join(_BASE, "component", "component.js"),
                         mimetype="application/javascript")

    @app.get("/s/<sid>/flights/<int:fid>")
    def flight_detail(sid, fid):
        conn = _conn_or_404(sid)
        try:
            f = conn.execute("SELECT * FROM flights WHERE id = ?", (fid,)).fetchone()
            if f is None:
                abort(404)
            meta = sessions.read_meta(conn)
            return render_template("detail.html", flight=dict(f),
                                   labels=meta["labels"], **_page_ctx(conn, sid))
        finally:
            conn.close()

    @app.post("/s/<sid>/flights/<int:fid>/book")
    def book(sid, fid):
        conn = _conn_or_404(sid)
        try:
            if conn.execute("SELECT 1 FROM flights WHERE id = ?", (fid,)).fetchone() is None:
                abort(404)
            cur = conn.execute(
                "INSERT INTO bookings (flight_id, name, email, created_at) VALUES (?,?,?,?)",
                (fid, request.form.get("name", ""), request.form.get("email", ""),
                 time.time()))
            conn.commit()
            return redirect(f"/s/{sid}/bookings/{cur.lastrowid}")
        finally:
            conn.close()

    @app.get("/s/<sid>/bookings/<int:bid>")
    def confirmation(sid, bid):
        conn = _conn_or_404(sid)
        try:
            b = conn.execute(
                "SELECT b.*, f.flight_no, f.price FROM bookings b "
                "JOIN flights f ON f.id = b.flight_id WHERE b.id = ?",
                (bid,)).fetchone()
            if b is None:
                abort(404)
            meta = sessions.read_meta(conn)
            return render_template("confirm.html", booking=dict(b),
                                   labels=meta["labels"], **_page_ctx(conn, sid))
        finally:
            conn.close()

    return app


if __name__ == "__main__":
    create_app().run(port=int(os.environ.get("PORT", 5001)), threaded=True)
