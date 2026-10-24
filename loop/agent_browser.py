"""Use A's browser strategies for both sandbox episodes and real transfer.

browser:llm uses A's model provider; browser:auto is an explicitly scripted
memory-driven rehearsal. Every episode owns its browser context. The real
checker reads the cart before that context is closed.
"""
import asyncio
from urllib.parse import urljoin, urlsplit

from .agent import Agent, EpisodeResult


class BrowserAgent(Agent):
    def __init__(self, strategy="llm", spec=None):
        if strategy not in ("naive", "smart", "auto", "llm"):
            raise ValueError(f"unknown browser strategy {strategy}")
        self.strategy = strategy
        self.spec = spec or {}
        self.agent_id = f"browser:{strategy}"

    async def run(self, env, task, session, skill_notes=""):
        return await asyncio.to_thread(self._run, env, task, session, skill_notes)

    def _run(self, env, task, session, notes):
        from playwright.sync_api import sync_playwright
        from capture import agents, checker, llm

        if self.strategy != "llm" and task.get("params"):
            raise ValueError("scripted browser strategies support shop-cheapest only")
        entry = session["entry_url"]
        if env is not None:
            if env._test_client is not None:
                raise ValueError("browser agents need a running HTTP server")
            entry = urljoin(env.base_url + "/", entry)
        actions = []

        def record(action, failure_hint=False):
            actions.append(action)

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                context = browser.new_context(viewport={"width": 1400, "height": 900})
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
                    provider = llm.default_llm()
                    if provider is None:
                        raise RuntimeError("browser:llm requires an LLM API key")
                    agents.run_llm(page, record, task["instruction"], provider,
                                   notes, entry_url=entry)
                elif self.strategy == "auto":
                    agents.run_auto(page, record, task["instruction"], notes,
                                    entry_url=entry)
                else:
                    agents.STRATEGIES[self.strategy](
                        page, record, task["instruction"], entry_url=entry)
                verdict = None
                if env is None:
                    verdict = checker.verify(page)
                    record(f"real-site checker: {verdict['reason']}")
                return EpisodeResult(actions, verification=verdict)
            finally:
                browser.close()
