"""Rollout core — importable by run_rollouts, repro_check, improve, transfer.

One episode = reset(seed) -> agent.run(instruction, entry_url) -> verify ->
a rollouts.jsonl record. Episodes run under an asyncio semaphore; each gets
its own EnvClient so an in-process Flask test client stays thread-safe and
per-session env DBs make collisions impossible.

Baseline caching: records keyed by (task, seed, agent, memory hash) — the
baseline round is computed once and reused, never re-run.
"""
import asyncio
import hashlib
import json
import os

from .agent import EpisodeResult

ROLLOUT_KEYS = ("round", "task_id", "seed", "passed", "reason", "steps")


def cache_key(task_id, seed, agent_id, skill_notes):
    mh = hashlib.sha1((skill_notes or "").encode()).hexdigest()[:8]
    return f"{task_id}:{seed}:{agent_id}:{mh}"


def load_cache(path):
    if path and os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}


def save_cache(path, cache):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(cache, f, indent=2)


def append_rollouts(path, records):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


async def run_episode(client, agent, task, seed, round_no, overrides=None,
                      skill_notes="", timeout=120, phase=None):
    """One episode -> a rollouts.jsonl record (never raises)."""
    rec = {"round": round_no, "task_id": task["id"], "seed": seed,
           "agent": agent.agent_id}
    if phase:
        rec["phase"] = phase
    try:
        session = await asyncio.to_thread(
            client.reset, seed=seed, **(overrides or {}))
    except Exception as e:
        return {**rec, "passed": False, "reason": f"error: reset: {e}",
                "steps": 0}
    sid = session["session_id"]
    rec["session_id"] = sid

    try:
        res = await asyncio.wait_for(
            agent.run(client, task, session, skill_notes=skill_notes),
            timeout)
    except asyncio.TimeoutError:
        res = EpisodeResult(ok=False, error=f"timeout after {timeout}s")
    except Exception as e:
        res = EpisodeResult(ok=False, error=str(e))
    rec["steps"] = res.steps
    rec["actions"] = res.actions

    if not res.ok:
        rec.update(passed=False, reason=f"error: {res.error}")
        return rec
    try:
        verdict = await asyncio.to_thread(client.verify, task["id"], sid)
    except Exception as e:
        verdict = {"passed": False, "reason": f"error: verify: {e}"}
    rec.update(passed=bool(verdict.get("passed")),
               reason=verdict.get("reason", ""))
    return rec


async def run_round(client_factory, agent, task, seeds, round_no,
                    overrides=None, skill_notes="", timeout=120,
                    concurrency=8, phase=None, cache=None, cache_path=None):
    """Parallel episodes over seeds -> [records] in seed order.

    cache: dict of previously computed records (baseline reuse). New
    records are written back and persisted to cache_path if given.
    """
    sem = asyncio.Semaphore(concurrency)

    async def one(seed):
        context = json.dumps({"task": task, "spec": getattr(agent, "spec", {}),
                              "overrides": overrides or {}}, sort_keys=True)
        key = cache_key(task["id"], seed, agent.agent_id, skill_notes) + ":" + \
            hashlib.sha256(context.encode()).hexdigest()[:16]
        if cache is not None and key in cache:
            cached = cache[key]
            try:
                await asyncio.to_thread(client_factory().get_state, cached["session_id"])
            except Exception:
                pass  # The server restarted or session expired; rerun it.
            else:
                return {**cached, "round": round_no,
                        "phase": phase or cached.get("phase")}
        async with sem:
            rec = await run_episode(client_factory(), agent, task, seed,
                                    round_no, overrides, skill_notes,
                                    timeout, phase)
        rec["round"] = round_no
        if cache is not None:
            cache[key] = rec
        return rec

    records = list(await asyncio.gather(*[one(s) for s in seeds]))
    if cache is not None and cache_path:
        save_cache(cache_path, cache)
    return records
