"""Agent harness — run a task on the real site, log every step, verify outcome.

    python -m capture.run_agent --strategy naive \
        --out fixtures/trajectory.json --run-name naive

Writes the contract artifact (trajectory.json) plus per-step screenshots and DOM
snapshots under --media-dir (default fixtures/shots/<run-name>/ and
fixtures/dom/<run-name>/).

Strategies: naive | smart (scripted, no LLM key needed) | llm (needs
ANTHROPIC_API_KEY / OPENAI_API_KEY). C's rollouts and the transfer run reuse
this same harness (``--memory path/to/skill_notes.md`` injects distilled memory).
"""

import argparse
import json
import sys
import time
import hashlib
from pathlib import Path

from playwright.sync_api import sync_playwright

from . import agents, checker, llm as llm_mod
from .site_config import SITE_URL, TASK

REPO_ROOT = Path(__file__).resolve().parent.parent


def _rel(p: Path) -> str:
    try:
        return p.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return p.as_posix()


def run(task, strategy, out_path, run_name, headless=True, video=False,
        memory_path=None, max_steps=30, media_dir=None):
    media_root = Path(media_dir) if media_dir else REPO_ROOT / "fixtures"
    shots_dir = media_root / "shots" / run_name
    dom_dir = media_root / "dom" / run_name
    video_dir = media_root / "video" / run_name
    for d in (shots_dir, dom_dir):
        d.mkdir(parents=True, exist_ok=True)
    if video:
        video_dir.mkdir(parents=True, exist_ok=True)

    steps = []

    llm_fn = memory = model = None
    if strategy == "llm":
        model = llm_mod.resolve_config()
        llm_fn = llm_mod.make_llm(model)
    if memory_path:
        memory = Path(memory_path).read_text()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx = browser.new_context(
            viewport={"width": 1400, "height": 900},
            record_video_dir=str(video_dir) if video else None,
        )
        page = ctx.new_page()

        failure_step_hint = None

        def rec(action, failure_hint=False):
            nonlocal failure_step_hint
            i = len(steps)
            shot = shots_dir / f"{i:03d}.png"
            dom = dom_dir / f"{i:03d}.html"
            try:
                page.screenshot(path=str(shot), full_page=False)
            except Exception:
                shot = None
            try:
                dom.write_text(page.content())
            except Exception:
                dom = None
            steps.append({
                "i": i,
                "action": action,
                "url": page.url,
                "screenshot_path": _rel(shot) if shot else None,
                "dom_path": _rel(dom) if dom else None,
            })
            if failure_hint:
                failure_step_hint = i
            print(f"  step {i:02d}: {action}  [{page.url}]", flush=True)

        t0 = time.time()
        fn = agents.STRATEGIES.get(strategy)
        if fn:
            fn(page, rec, task)
        elif strategy == "auto":
            agents.run_auto(page, rec, task, memory or "")
        elif strategy == "llm":
            agents.run_llm(page, rec, task, llm_fn, memory or "", max_steps)
        else:
            raise SystemExit(f"unknown strategy {strategy!r}")

        # --- verify outcome on the real site -------------------------------
        expected = checker.expected_cheapest()
        print(f"  expected (live argmin): {expected['name']} "
              f"${expected['price']} [{expected['id']}]")
        result = checker.verify(page, expected)
        rec(f"checker: {result['reason']}")

        ctx.close()
        video_path = _rel(Path(page.video.path())) if video and page.video else None
        browser.close()

    failed = not result["passed"]
    trajectory = {
        "task": task,
        "site_url": SITE_URL,
        "agent": {"strategy": strategy, "memory": bool(memory), "max_steps": max_steps,
                  "policy_version": agents.POLICY_VERSION,
                  **(model.metadata() if model else {"provider": "scripted"})},
        "memory_hash": hashlib.sha256((memory or "").encode()).hexdigest(),
        "video_path": video_path,
        "steps": steps,
        "outcome": "failed" if failed else "passed",
        "failure_step": (failure_step_hint if failed and failure_step_hint is not None
                         else (len(steps) - 2 if failed else None)),
        "checker": result,
        "expected": expected,
        "duration_s": round(time.time() - t0, 1),
    }
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(trajectory, indent=2))
    print(f"\n{result['passed'] and 'PASSED' or 'FAILED'}: {result['reason']}")
    print(f"trajectory -> {_rel(out_path)} ({len(steps)} steps)")
    return trajectory


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", default=TASK)
    ap.add_argument("--strategy", default="naive",
                    choices=["naive", "smart", "auto", "llm"])
    ap.add_argument("--out", default=str(REPO_ROOT / "fixtures/trajectory.json"))
    ap.add_argument("--run-name", default="naive")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--video", action="store_true",
                    help="record playwright video (fallback demo footage)")
    ap.add_argument("--memory", default=None, help="skill-notes file injected "
                    "into the LLM agent's prompt (transfer run)")
    ap.add_argument("--max-steps", type=int, default=30)
    ap.add_argument("--media-dir", default=None)
    a = ap.parse_args(argv)
    t = run(a.task, a.strategy, Path(a.out), a.run_name,
            headless=not a.headed, video=a.video,
            memory_path=a.memory, max_steps=a.max_steps, media_dir=a.media_dir)
    sys.exit(0 if t["outcome"] == "passed" else 1)


if __name__ == "__main__":
    main()
