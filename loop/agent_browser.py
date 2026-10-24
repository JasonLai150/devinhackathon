"""Use A's browser strategies for both sandbox episodes and real transfer.

browser:llm uses A's model provider; browser:auto is an explicitly scripted
memory-driven rehearsal. Every episode owns its browser context. The real
checker reads the cart before that context is closed.
"""
import asyncio
import hashlib
import threading
import time
import uuid
from urllib.parse import urljoin, urlsplit

from .agent import Agent, EpisodeResult


class BrowserAgent(Agent):
    def __init__(self, strategy="llm", spec=None, model_config=None, llm_fn=None,
                 max_steps=30, artifact_dir=None, headed=False, slow_mo=0):
        if strategy not in ("naive", "smart", "auto", "llm"):
            raise ValueError(f"unknown browser strategy {strategy}")
        self.strategy = strategy
        self.spec = spec or {}
        self.agent_id = f"browser:{strategy}"
        self.model_config = model_config
        self.llm_fn = llm_fn
        self.max_steps = max_steps
        self.artifact_dir = artifact_dir
        self.headed = headed
        self.slow_mo = slow_mo
        if strategy == "llm" and llm_fn is None:
            from capture import llm
            self.model_config = model_config or llm.resolve_config()
            self.llm_fn = llm.make_llm(self.model_config)

    @property
    def metadata(self):
        from capture.agents import POLICY_VERSION
        return {"strategy": self.strategy, "max_steps": self.max_steps,
                "policy_version": POLICY_VERSION,
                **(self.model_config.metadata() if self.model_config else
                   {"provider": "injected-test" if self.llm_fn else "scripted", "model": None})}

    async def run(self, env, task, session, skill_notes=""):
        cancelled = threading.Event()
        try:
            return await asyncio.to_thread(self._run, env, task, session, skill_notes, cancelled)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    def _run(self, env, task, session, notes, cancelled):
        from playwright.sync_api import sync_playwright
        from capture import agents, checker
        from capture.recording import BrowserRecording, artifact_ref

        if self.strategy != "llm" and task.get("params"):
            raise ValueError("scripted browser strategies support shop-cheapest only")
        entry = session["entry_url"]
        if env is not None:
            if env._test_client is not None:
                raise ValueError("browser agents need a running HTTP server")
            entry = urljoin(env.base_url + "/", entry)
        actions = []
        started = time.monotonic()
        episode_id = uuid.uuid4().hex
        recording = BrowserRecording(self.artifact_dir, episode_id) if self.artifact_dir else None
        video_path, trajectory_path, verdict, error = None, None, None, None

        def record(action, failure_hint=False):
            if cancelled.is_set():
                raise RuntimeError("episode cancelled")
            actions.append(action)
            if recording:
                recording.record(page, action)

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=not self.headed, slow_mo=self.slow_mo)
            try:
                context = browser.new_context(viewport={"width": 1400, "height": 900},
                    **({"record_video_dir": str(recording.directory)} if recording else {}))
                if env is not None:
                    # Runner-only /state, /verify and other sessions are not browser tools.
                    prefix = f"/s/{session['session_id']}/"
                    origin = urlsplit(entry).netloc
                    def scoped(route):
                        url = urlsplit(route.request.url)
                        if url.netloc == origin and url.path.startswith(prefix):
                            route.continue_()
                        else:
                            route.abort()
                    context.route("**/*", scoped)
                page = context.new_page()
                page.set_default_timeout(15000)
                if self.strategy == "llm":
                    agents.run_llm(page, record, task["instruction"], self.llm_fn,
                                   notes, max_steps=self.max_steps, entry_url=entry)
                elif self.strategy == "auto":
                    agents.run_auto(page, record, task["instruction"], notes,
                                    entry_url=entry)
                else:
                    agents.STRATEGIES[self.strategy](
                        page, record, task["instruction"], entry_url=entry)
                if env is None:
                    if task.get("params"):
                        raise ValueError("real-site checker supports shop-cheapest only")
                    verdict = checker.verify(page)
                    record(f"real-site checker: {verdict['reason']}")
            except Exception as exc:
                error = str(exc)
            finally:
                if "context" in locals():
                    context.close()
                if recording and "page" in locals() and page.video:
                    video_path = artifact_ref(page.video.path())
                browser.close()
        if recording:
            trajectory_path = recording.finish(
                task["instruction"], entry, self.metadata,
                hashlib.sha256(notes.encode()).hexdigest(), verdict, error, video_path)
        return EpisodeResult(actions, ok=error is None, error=error, verification=verdict,
                             episode_id=episode_id, trajectory_path=trajectory_path,
                             video_path=video_path, agent_metadata=self.metadata,
                             duration_s=round(time.monotonic() - started, 2))
