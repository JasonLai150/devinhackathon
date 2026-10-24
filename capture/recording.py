"""Browser evidence shared by sandbox rollouts and real-site runs."""
import json
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def listing_observation(page):
    """Persist the visible listing evidence used to compare failure causes."""
    from .agents import _cards, _current_page
    products = _cards(page)
    return {"page": _current_page(page), "products": products}


def artifact_ref(path):
    path = Path(path).resolve()
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


class BrowserRecording:
    def __init__(self, directory, episode_id=None):
        self.episode_id = episode_id or uuid.uuid4().hex
        self.directory = Path(directory) / self.episode_id
        self.directory.mkdir(parents=True, exist_ok=False)
        self.steps = []
        self.started = time.monotonic()

    def record(self, page, action):
        index = len(self.steps)
        shot = self.directory / f"{index:03d}.png"
        dom = self.directory / f"{index:03d}.html"
        page.screenshot(path=str(shot))
        dom.write_text(page.content())
        self.steps.append({"i": index, "action": action, "url": page.url,
                           "screenshot_path": artifact_ref(shot), "dom_path": artifact_ref(dom),
                           "listing": listing_observation(page)})

    def finish(self, task, entry, metadata, notes_hash, verdict, error, video_path=None,
               observations=None):
        trajectory = {
            "episode_id": self.episode_id, "task": task, "site_url": entry,
            "agent": metadata, "memory_hash": notes_hash,
            "steps": self.steps, "video_path": artifact_ref(video_path) if video_path else None,
            "outcome": "error" if error else ("passed" if verdict and verdict["passed"] else
                                            "failed" if verdict else "unverified"),
            "checker": verdict, "error": error,
            "observations": observations or [],
            "duration_s": round(time.monotonic() - self.started, 2),
            "failure_step": next((s["i"] for s in reversed(self.steps)
                                  if "add to cart" in s["action"].lower()), None)
                            if verdict and not verdict["passed"] else None,
        }
        path = self.directory / "trajectory.json"
        path.write_text(json.dumps(trajectory, indent=2))
        return artifact_ref(path)


def attach_verdict(path, verdict):
    """Runner owns sandbox verification; attach it after the browser closes."""
    if not path:
        return
    target = ROOT / path
    trajectory = json.loads(target.read_text())
    trajectory.update(checker=verdict, outcome="passed" if verdict["passed"] else "failed")
    target.write_text(json.dumps(trajectory, indent=2))
