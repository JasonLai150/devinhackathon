"""Agent interface for rollouts + the CLI factory.

The runner owns reset/verify; the agent owns everything between: landing
on entry_url, browsing, and committing the mutation. run() receives the
EnvClient (sim agents drive it; browser agents ignore it — their own
Playwright context hits entry_url) plus skill_notes distilled from earlier
successes, which the agent injects into its own behavior.
"""
import asyncio
from dataclasses import dataclass, field


@dataclass
class EpisodeResult:
    actions: list = field(default_factory=list)  # trace of urls/actions
    ok: bool = True
    error: str = None

    @property
    def steps(self):
        return len(self.actions)


class Agent:
    """Base class. Subclasses: SimAgent (deterministic strategies),
    BrowserAgent (A's real harness, post-kickoff)."""

    agent_id = "agent"

    async def run(self, env, task, session, skill_notes=""):
        raise NotImplementedError


def make_agent(name, spec=None):
    """CLI agent names: sim:naive | sim:smart | sim:auto | browser."""
    if name.startswith("sim:"):
        if spec is None:
            raise ValueError("sim agents need the env spec (--spec)")
        from .sim_agent import SimAgent
        return SimAgent(spec, strategy=name.split(":", 1)[1])
    if name == "browser":
        from .agent_browser import BrowserAgent
        return BrowserAgent()
    raise ValueError(
        f"unknown agent {name!r} (expected sim:naive|sim:smart|sim:auto|browser)")
