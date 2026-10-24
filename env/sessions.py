"""Session lifecycle: each rollout gets its own SQLite file under .sessions/.

Stateless server design — the session id maps directly to a file path, so
/reset, /state, /verify and agent traffic never share mutable state. This is
what makes C's parallel rollouts safe.
"""
import json
import os
import secrets
import sqlite3
import time

_DEFAULT_DIR = os.path.join(os.path.dirname(__file__), ".sessions")


def sessions_dir():
    return os.environ.get("ENV_SESSIONS_DIR", _DEFAULT_DIR)


def db_path(sid):
    return os.path.join(sessions_dir(), f"{sid}.db")


def exists(sid):
    return os.path.exists(db_path(sid))


def connect(sid):
    """Open a per-request connection, or None if the session doesn't exist."""
    if not exists(sid):
        return None
    conn = sqlite3.connect(db_path(sid))
    conn.row_factory = sqlite3.Row
    return conn


def create_session(seed, spec, overrides=None):
    """Create + seed a new session DB. Returns the session id."""
    from . import seeds

    os.makedirs(sessions_dir(), exist_ok=True)
    sid = secrets.token_urlsafe(8)
    conn = sqlite3.connect(db_path(sid))
    conn.row_factory = sqlite3.Row
    seeds.seed_db(conn, seed, spec, overrides or {})
    conn.commit()
    conn.close()
    return sid


def read_meta(conn):
    return {r["k"]: json.loads(r["v"]) for r in conn.execute("SELECT k, v FROM meta")}


def list_sessions():
    out = []
    if not os.path.isdir(sessions_dir()):
        return out
    for f in sorted(os.listdir(sessions_dir())):
        if not f.endswith(".db"):
            continue
        conn = connect(f[:-3])
        if conn is None:
            continue
        try:
            meta = read_meta(conn)
            out.append({"session_id": f[:-3], "seed": meta.get("seed"),
                        "created_at": meta.get("created_at")})
        finally:
            conn.close()
    return out


def destroy(sid):
    if exists(sid):
        os.remove(db_path(sid))
