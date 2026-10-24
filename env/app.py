"""Failure-to-Env mini-env: deterministic Flask skeleton + component slot.

Endpoints (contract with C):
  POST /reset?seed=N[&popup=0|1&labels=i]  -> {session_id, entry_url}
  GET  /s/<sid>/...                        -> the env; agent browses here
  GET  /state?session=<sid>                -> full DB dump + meta
  POST /verify {task_id, session_id}       -> {passed, reason}
  GET  /sessions                           -> session list (dashboard/debug)

All env routes are driven by spec.routes — the skeleton knows only the
roles "list" (entry page containing the component slot), "detail",
"mutation" (POST), and "confirmation". Domain wiring comes from the spec.

Route path conventions (spec.routes):
  detail:       ".../<int:eid>"   eid = entity row id
  mutation:     ".../<int:eid>/..." same eid, becomes the fk value
  confirmation: ".../<int:bid>"   bid = mutation row id
"""
import json
import os
import random
import re
import time

from flask import Flask, abort, jsonify, redirect, request, send_file
from flask.templating import render_template
from jinja2 import ChoiceLoader, FileSystemLoader

from . import sessions, stubs, verifiers

_BASE = os.path.dirname(__file__)
_DEFAULT_SPEC = os.path.join(_BASE, "spec", "env_spec.json")
_DEFAULT_COMPONENT_DIR = os.path.join(_BASE, "component")

_CONVERTER_RE = re.compile(r"<int:\w+>")


def fill_path(path_template, **ids):
    """'/flights/<int:eid>' + eid=24 -> '/flights/24'"""
    def sub(m):
        name = m.group(0)[5:-1]  # strip '<int:' and '>'
        if name not in ids:
            raise KeyError(f"no id provided for route param {name}")
        return str(ids[name])
    return _CONVERTER_RE.sub(sub, path_template)


def create_app(spec_path=None, component_dir=None):
    app = Flask(__name__)
    spec_path = spec_path or os.environ.get("ENV_SPEC", _DEFAULT_SPEC)
    with open(spec_path) as f:
        spec = app.config["SPEC"] = json.load(f)
    component_dir = component_dir or os.environ.get(
        "ENV_COMPONENT_DIR", _DEFAULT_COMPONENT_DIR)
    app.config["COMPONENT_DIR"] = component_dir

    # Component slot: component/ is a second template-search path so the
    # generator drops component.html/js there without touching templates/.
    app.jinja_loader = ChoiceLoader([
        FileSystemLoader(os.path.join(_BASE, "templates")),
        FileSystemLoader(component_dir),
    ])

    stubs.register_stubs(app, spec.get("backend_calls", []))
    routes = spec["routes"]

    def _conn_or_404(sid):
        conn = sessions.connect(sid)
        if conn is None:
            abort(404)
        return conn

    def _page_ctx(conn, sid):
        return {"sid": sid, "meta": sessions.read_meta(conn)}

    def _has_component():
        return os.path.exists(os.path.join(component_dir, "component.html"))

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

    @app.after_request
    def _cors_sessions(resp):
        # Dashboard (served from another localhost port) reads /sessions.
        if request.path == "/sessions":
            resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp

    @app.get("/")
    def root():
        return jsonify({"service": "failure-to-env", "spec": spec.get("name")})

    @app.post("/reset")
    def reset():
        seed = request.args.get("seed", type=int)
        if seed is None:
            seed = random.randint(0, 2**31 - 1)
        overrides = {k: request.args[k] for k in ("popup", "labels")
                     if k in request.args}
        sid = sessions.create_session(seed, spec, overrides)
        return jsonify({"session_id": sid, "entry_url": f"/s/{sid}{routes['entry']}",
                        "seed": seed})

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
                                        body.get("session_id", ""), spec))

    # ---- spec-driven env routes ----

    @app.get(f"/s/<sid>{routes['entry']}")
    def entry(sid):
        conn = _conn_or_404(sid)
        try:
            meta = sessions.read_meta(conn)
            return render_template("index.html", labels=meta["labels"],
                                   has_component=_has_component(),
                                   **_page_ctx(conn, sid))
        finally:
            conn.close()

    @app.get("/s/<sid>/component.js")
    def component_js(sid):
        if not sessions.exists(sid):
            abort(404)
        path = os.path.join(component_dir, "component.js")
        if not os.path.exists(path):
            abort(404)
        return send_file(path, mimetype="application/javascript")

    def _detail_view():
        entity = routes["detail"]["entity"]

        def view(sid, eid):
            conn = _conn_or_404(sid)
            try:
                row = conn.execute(
                    f"SELECT * FROM {entity} WHERE id = ?", (eid,)).fetchone()
                if row is None:
                    abort(404)
                meta = sessions.read_meta(conn)
                mut_table = routes["mutation"]["table"]
                form_fields = spec["mutations"][mut_table].get("form_fields", [])
                action = f"/s/{sid}" + fill_path(routes["mutation"]["path"], eid=eid)
                return render_template(
                    "detail.html", row=dict(row), entity=entity,
                    title_field=routes["detail"].get("title_field"),
                    form_fields=form_fields, action=action,
                    labels=meta["labels"], **_page_ctx(conn, sid))
            finally:
                conn.close()
        return view

    def _mutation_view():
        mut = routes["mutation"]
        entity = routes["detail"]["entity"]

        def view(sid, eid):
            conn = _conn_or_404(sid)
            try:
                if conn.execute(
                        f"SELECT 1 FROM {entity} WHERE id = ?", (eid,)).fetchone() is None:
                    abort(404)
                table = mut["table"]
                fields = [f["name"] for f in
                          spec["mutations"][table].get("form_fields", [])]
                cols = [mut["fk_column"], *fields, "created_at"]
                vals = [eid, *[request.form.get(f, "") for f in fields], time.time()]
                cur = conn.execute(
                    f"INSERT INTO {table} ({', '.join(cols)}) "
                    f"VALUES ({', '.join('?' for _ in cols)})", vals)
                conn.commit()
                return redirect(
                    f"/s/{sid}" + fill_path(routes["confirmation"]["path"],
                                            bid=cur.lastrowid))
            finally:
                conn.close()
        return view

    def _confirmation_view():
        conf = routes["confirmation"]

        def view(sid, bid):
            conn = _conn_or_404(sid)
            try:
                row = conn.execute(
                    f"SELECT * FROM {conf['table']} WHERE id = ?", (bid,)).fetchone()
                if row is None:
                    abort(404)
                joined = None
                if "join" in conf:
                    j = conf["join"]
                    joined = conn.execute(
                        f"SELECT * FROM {j['table']} WHERE id = ?",
                        (row[j["fk"]],)).fetchone()
                    joined = {f: joined[f] for f in j["fields"]} if joined else None
                meta = sessions.read_meta(conn)
                return render_template(
                    "confirm.html", mutation=dict(row), table=conf["table"],
                    joined=joined, labels=meta["labels"], **_page_ctx(conn, sid))
            finally:
                conn.close()
        return view

    app.add_url_rule(f"/s/<sid>{routes['detail']['path']}",
                     "detail", _detail_view())
    app.add_url_rule(f"/s/<sid>{routes['mutation']['path']}",
                     "mutation", _mutation_view(),
                     methods=[routes["mutation"].get("method", "POST")])
    app.add_url_rule(f"/s/<sid>{routes['confirmation']['path']}",
                     "confirmation", _confirmation_view())

    return app


if __name__ == "__main__":
    create_app().run(port=int(os.environ.get("PORT", 5001)), threaded=True)
