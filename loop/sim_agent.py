"""Scripted strategy agent — B's simulate_agent strategies, transport-agnostic.

  naive: reads only page 1 of the listing API and books the best match
         visible -> reproduces the pagination failure (mostly FAILs)
  smart: paginates through every page before choosing -> the fixed behavior
  auto:  acts naive until skill_notes contain the pagination cue -> models
         the improvement loop's memory injection deterministically

Same code path whether the EnvClient is a live server or an in-process
Flask app. Doubles as the deterministic demo fallback (red -> green with
no LLM or live agent needed).
"""
import asyncio
import re

from .agent import Agent, EpisodeResult

PAGINATION_CUE = "paginate"  # in skill_notes -> auto acts smart
_ID_RE = re.compile(r"<int:\w+>")


class SimAgent(Agent):
    def __init__(self, spec, strategy="naive"):
        if strategy not in ("naive", "smart", "auto"):
            raise ValueError(f"bad sim strategy {strategy!r}")
        self.spec = spec
        self.strategy = strategy
        self.agent_id = f"sim:{strategy}"

    async def run(self, env, task, session, skill_notes=""):
        return await asyncio.to_thread(
            self._run_sync, env, task, session, skill_notes)

    def _effective(self, skill_notes):
        if self.strategy == "auto":
            return "smart" if PAGINATION_CUE in (skill_notes or "").lower() \
                else "naive"
        return self.strategy

    def _run_sync(self, env, task, session, skill_notes):
        sid = session["session_id"]
        actions = [session["entry_url"]]
        status, _ = env.get(session["entry_url"])
        if status != 200:
            return EpisodeResult(actions, ok=False, error=f"entry -> {status}")

        api = self.spec["backend_calls"][0]["path"]
        first = env.get_json(f"/s/{sid}{api}", params={"page": 1})
        actions.append(f"{api}?page=1")
        items = list(first["items"])

        if self._effective(skill_notes) == "smart":
            for p in range(2, first["total_pages"] + 1):
                items += env.get_json(
                    f"/s/{sid}{api}", params={"page": p})["items"]
                actions.append(f"{api}?page={p}")

        pick = self._pick(items, task.get("params", {}))
        if pick is None:
            return EpisodeResult(actions)  # saw no match; verify will fail it

        routes = self.spec["routes"]
        detail = _ID_RE.sub(str(pick["id"]), routes["detail"]["path"])
        env.get(f"/s/{sid}{detail}")
        actions.append(detail)

        mut = routes["mutation"]
        table = mut["table"]
        form = {f["name"]: "sim@agent.dev" for f in
                self.spec["mutations"][table].get("form_fields", [])}
        path = _ID_RE.sub(str(pick["id"]), mut["path"])
        env.post_form(f"/s/{sid}{path}", form)
        actions.append(f"POST {path}")
        return EpisodeResult(actions)

    @staticmethod
    def _pick(items, params):
        """Best item by task constraints over what the agent has SEEN —
        a naive agent only sees page 1 (that's the failure)."""
        pool = items
        if params.get("direct_only"):
            pool = [f for f in pool if f["direct"] == 1]
        if params.get("departs_after"):
            pool = [f for f in pool if f["departs_at"] >= params["departs_after"]]
        if not pool:
            return None
        sort_by = params.get("sort_by", "price")
        return sorted(pool, key=lambda f: (f[sort_by], f["price"], f["id"]),
                      reverse=params.get("order") == "desc")[0]
