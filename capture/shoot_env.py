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


def shoot(seed=42, env_url=None, out_path=None):
    env = dict(os.environ,
               ENV_SPEC=str(REPO_ROOT / "fixtures/env_spec.json"),
               ENV_COMPONENT_DIR=str(REPO_ROOT / "fixtures/captured"))
    proc = None
    if env_url is None:
        proc = subprocess.Popen(
            [sys.executable, "-m", "env.app"], cwd=REPO_ROOT, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    env_url = (env_url or f"http://localhost:{PORT}").rstrip("/")
    try:
        for _ in range(40):
            try:
                urllib.request.urlopen(env_url + "/", timeout=1)
                break
            except Exception:
                time.sleep(0.5)
        req = urllib.request.Request(
            f"{env_url}/reset?seed={seed}&popup=0", method="POST")
        sid = json.load(urllib.request.urlopen(req))["session_id"]

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.goto(f"{env_url}/s/{sid}/",
                      wait_until="domcontentloaded")
            page.wait_for_timeout(2500)
            try:
                page.click("text=No thanks", timeout=2500)
                page.wait_for_timeout(1800)
            except Exception:
                pass
            out = Path(out_path) if out_path else REPO_ROOT / "fixtures/shots/env/index.png"
            out.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(out))
            browser.close()
            print(f"env screenshot -> {out} (session {sid})")
    finally:
        if proc is not None:
            proc.terminate()
            proc.wait(timeout=5)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--env-url", default=None, help="use an already running environment")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    shoot(args.seed, args.env_url, args.out)
