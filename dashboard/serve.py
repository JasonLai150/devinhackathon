"""Serve the repo root so the dashboard can fetch artifacts.

    python dashboard/serve.py          # http://localhost:8000/dashboard/
    PORT=8080 python dashboard/serve.py
"""

import http.server
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = int(os.environ.get("PORT", 8000))

if __name__ == "__main__":
    os.chdir(ROOT)
    print(f"serving {ROOT} on http://localhost:{PORT}/dashboard/")
    http.server.ThreadingHTTPServer(
        ("127.0.0.1", PORT), http.server.SimpleHTTPRequestHandler
    ).serve_forever()
