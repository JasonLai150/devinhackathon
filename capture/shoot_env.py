"""Screenshot the running mini-env for the dashboard's side-by-side panel.

Boots env.app against the REAL spec + captured component (env vars), resets a
session, screenshots the entry page -> fixtures/shots/env/index.png.

    python -m capture.shoot_env [--seed 42]
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO_ROOT = Path(__file__).resolve().parent.parent
PORT = 5001


def shoot(seed=42):
    env = dict(os.environ,
               ENV_SPEC=str(REPO_ROOT / "fixtures/env_spec.json"),
               ENV_COMPONENT_DIR=str(REPO_ROOT / "fixtures/captured"))
    proc = subprocess.Popen(
        [sys.executable, "-m", "env.app"], cwd=REPO_ROOT, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(40):
            try:
                urllib.request.urlopen(f"http://localhost:{PORT}/", timeout=1)
                break
            except Exception:
                time.sleep(0.5)
        req = urllib.request.Request(
            f"http://localhost:{PORT}/reset?seed={seed}", method="POST")
        sid = json.load(urllib.request.urlopen(req))["session_id"]

        with sync_playwright() as p:
            page = p.chromium.launch().new_page(
                viewport={"width": 1400, "height": 900})
            page.goto(f"http://localhost:{PORT}/s/{sid}/",
                      wait_until="domcontentloaded")
            page.wait_for_timeout(2500)
            try:
                page.click("text=No thanks", timeout=2500)
                page.wait_for_timeout(1800)
            except Exception:
                pass
            out = REPO_ROOT / "fixtures/shots/env/index.png"
            out.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(out))
            print(f"env screenshot -> {out} (session {sid})")
    finally:
        proc.terminate()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=42)
    shoot(ap.parse_args().seed)
