"""Managed local HTTP server for sequential generation checks and experiments."""
import os
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

from werkzeug.serving import WSGIRequestHandler, make_server

from .app import create_app


class QuietHandler(WSGIRequestHandler):
    def log_request(self, *args, **kwargs):
        pass


@contextmanager
def running_env(spec_path, component_dir, session_dir=None):
    # Session configuration is process-wide today; use this for one active app
    # per process. Parallel episodes within that app remain session-isolated.
    old = os.environ.get("ENV_SESSIONS_DIR")
    with tempfile.TemporaryDirectory(prefix="failure-env-") as scratch:
        os.environ["ENV_SESSIONS_DIR"] = str(Path(session_dir or scratch).resolve())
        server, thread = None, None
        try:
            app = create_app(str(spec_path), str(component_dir))
            server = make_server("127.0.0.1", 0, app, threaded=True,
                                 request_handler=QuietHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            yield f"http://127.0.0.1:{server.server_port}", app
        finally:
            if server:
                server.shutdown()
                server.server_close()
            if thread:
                thread.join()
            if old is None:
                os.environ.pop("ENV_SESSIONS_DIR", None)
            else:
                os.environ["ENV_SESSIONS_DIR"] = old
