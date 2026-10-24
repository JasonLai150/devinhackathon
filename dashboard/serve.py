"""Serve the repo root so the dashboard can fetch artifacts.

    python dashboard/serve.py          # http://localhost:8000/dashboard/
    PORT=8080 python dashboard/serve.py
"""

import http.server
import os
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
PORT = int(os.environ.get("PORT", 8000))


class ArtifactHandler(http.server.SimpleHTTPRequestHandler):
    """Serve replay artifacts while keeping local credentials and Git private."""

    def send_head(self):
        if urlsplit(self.path).path == "/":
            self.send_response(302)
            self.send_header("Location", "/dashboard/")
            self.end_headers()
            return None
        parts = Path(unquote(urlsplit(self.path).path)).parts
        if any(part.startswith(".") and part != ".integration" for part in parts):
            self.send_error(404)
            return None
        target = Path(self.translate_path(self.path))
        if target.is_dir() and not (target / "index.html").is_file():
            self.send_error(404)
            return None
        return super().send_head()


if __name__ == "__main__":
    os.chdir(ROOT)
    print(f"serving {ROOT} on http://localhost:{PORT}/dashboard/")
    http.server.ThreadingHTTPServer(
        ("127.0.0.1", PORT), ArtifactHandler
    ).serve_forever()
