"""Stub routes for the captured component's backend calls.

Each spec.backend_calls entry becomes a route under /s/<sid>/<path> that
serves rows from the named session-DB table. Paginated stubs return
{items, page, per_page, total, total_pages} — the shape the dev-fixture
component and (later) generated components are written against.
"""
import math

from flask import abort, jsonify, request

from . import sessions


def _make_view(call):
    table = call["table"]
    paginated = call.get("paginated", False)
    default_per_page = call.get("per_page", 10)

    def view(sid):
        conn = sessions.connect(sid)
        if conn is None:
            abort(404)
        try:
            total = conn.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"]
            if not paginated:
                rows = conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()
                return jsonify({"items": [dict(r) for r in rows], "total": total})
            page = max(int(request.args.get("page", 1)), 1)
            per_page = min(int(request.args.get("per_page", default_per_page)), 100)
            rows = conn.execute(
                f"SELECT * FROM {table} ORDER BY id LIMIT ? OFFSET ?",
                (per_page, (page - 1) * per_page),
            ).fetchall()
            return jsonify({
                "items": [dict(r) for r in rows],
                "page": page,
                "per_page": per_page,
                "total": total,
                "total_pages": max(math.ceil(total / per_page), 1),
            })
        finally:
            conn.close()

    return view


def register_stubs(app, backend_calls):
    for call in backend_calls:
        rule = "/s/<sid>" + call["path"]
        app.add_url_rule(
            rule,
            endpoint=f"stub_{call['path'].strip('/').replace('/', '_')}",
            view_func=_make_view(call),
            methods=[call.get("method", "GET")],
        )
