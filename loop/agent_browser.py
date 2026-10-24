"""BrowserAgent — adapter shell for A's real agent harness.

Locked at kickoff (vision.md): one agent + model everywhere — A's capture
harness, C's rollouts, and the transfer run. A's runner plugs in here;
skill_notes are injected into the agent's system prompt before each
episode. Until the harness lands, run() raises so CLI plumbing can be
built and tested against sim:* agents.
"""
from .agent import Agent


class BrowserAgent(Agent):
    def __init__(self, harness=None, model=None):
        self.harness = harness      # A's run-agent callable, once it exists
        self.model = model
        self.agent_id = f"browser:{model or 'default'}"

    async def run(self, env, task, session, skill_notes=""):
        raise NotImplementedError(
            "BrowserAgent needs A's agent harness (browser-use or "
            "Playwright+LLM, locked at kickoff). Use --agent sim:* until then.")
