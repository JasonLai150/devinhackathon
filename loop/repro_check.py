"""Repro check (round 0) — does the baseline agent fail in the mini-env the
same way it failed on the real site?

Runs the baseline over repro seeds, then classifies every failed episode
against the spec's failure signature. For the pagination failure mode the
signature is: the agent never fetched listing page >= 2, or it booked an
item that was visible on page 1 — both readable from the session's
request_log + tables (runner-side /state; the agent never sees it).

Emits repro_report.json (the dashboard's credibility artifact), round-0
rollouts records, and a round-0 metrics.json entry.

  python -m loop.repro_check --agent sim:naive --task t2 --seeds 0-9 \\
      --in-process
"""
import argparse
import asyncio
import json
import os
import re

from . import config, runner
from .agent import make_agent
from .metrics import append_metric, success_rate
from .run_rollouts import _client_factory
from .seeds import parse_seeds


def classify(state, spec, record):
    """Label one episode's failure against the pagination signature."""
    call = spec["backend_calls"][0]
    api = call["path"].lstrip("/")
    per_page = call.get("per_page", 10)
    tables = state["tables"]

    page_re = re.compile(re.escape(api) + r"\?page=(\d+)")
    pages = [int(m.group(1)) for row in tables.get("request_log", [])
             for m in [page_re.search(row["path"])] if m]
    max_page = max(pages) if pages else 0

    mut = spec["routes"]["mutation"]
    bookings = tables.get(mut["table"], [])
    fk = mut["fk_column"]
    booked_id = bookings[-1][fk] if bookings else None
    entity = call["table"]
    page1_ids = {r["id"] for r in
                 sorted(tables[entity], key=lambda r: r["id"])[:per_page]}

    signals = {
        "max_listing_page_seen": max_page,
        "never_paginated": max_page < 2,
        "booked_from_page1": booked_id in page1_ids if booked_id else False,
    }
    signature_failure = (
        not record["passed"]
        and not record.get("reason", "").startswith("error:")
        and max_page == 1 and booked_id in page1_ids)
    return {**signals, "signature_failure": signature_failure}


async def repro(client_factory, agent, task, seeds, spec,
                round_no=0, cache=None, cache_path=None, timeout=120,
                concurrency=8, overrides=None):
    records = await runner.run_round(
        client_factory, agent, task, seeds, round_no,
        overrides=overrides, timeout=timeout, concurrency=concurrency,
        phase="repro", cache=cache, cache_path=cache_path)
    client = client_factory()
    episodes = []
    for rec in records:
        if rec["passed"] or "session_id" not in rec:
            episodes.append({**rec, "signature_failure": False})
            continue
        state = await asyncio.to_thread(client.get_state, rec["session_id"])
        episodes.append({**rec, **classify(state, spec, rec)})
    return episodes


def build_report(agent_id, task, spec, episodes, trajectory):
    failures = [e for e in episodes if not e["passed"]]
    sig = [e for e in failures if e["signature_failure"]]
    return {
        "task_id": task["id"],
        "agent": agent_id,
        "spec_failure_note": spec.get("failure_note"),
        "spec_failure_step": spec.get("failure_step"),
        "trajectory_failure_step": (trajectory or {}).get("failure_step"),
        "episodes": episodes,
        "n": len(episodes),
        "failures": len(failures),
        "signature_failures": len(sig),
        # Reproduced = every observed failure is the known failure mode.
        "reproduced": bool(sig) and len(sig) == len(failures),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(prog="loop.repro_check")
    ap.add_argument("--agent", default="sim:naive")
    ap.add_argument("--task", default="t2")
    ap.add_argument("--seeds", default="0-9")
    ap.add_argument("--env-url", default=None)
    ap.add_argument("--in-process", action="store_true")
    ap.add_argument("--tasks", default=None)
    ap.add_argument("--spec", default=None)
    ap.add_argument("--trajectory", default=None)
    ap.add_argument("--popup", default=None)
    ap.add_argument("--labels", default=None)
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--report", default=None, help="repro_report.json path")
    ap.add_argument("--rollouts", default=None, help="rollouts.jsonl path")
    ap.add_argument("--metrics", default=None, help="metrics.json path")
    args = ap.parse_args(argv)

    spec = config.load_spec(args.spec)
    task = config.load_tasks(args.tasks, spec)[args.task]
    agent = make_agent(args.agent, spec)
    traj_path = args.trajectory or config.trajectory_path()
    trajectory = config.load_json(traj_path) if os.path.exists(traj_path) \
        else None

    overrides = {k: v for k, v in
                 {"popup": args.popup, "labels": args.labels}.items()
                 if v is not None}
    cache, cache_path = None, None
    if not args.no_cache:
        cache_path = config.artifact_path("baseline_cache.json")
        cache = runner.load_cache(cache_path)

    episodes = asyncio.run(repro(
        _client_factory(args), agent, task, parse_seeds(args.seeds), spec,
        overrides=overrides, timeout=args.timeout,
        concurrency=args.concurrency, cache=cache, cache_path=cache_path))
    report = build_report(agent.agent_id, task, spec, episodes, trajectory)

    report_path = args.report or config.artifact_path("repro_report.json")
    os.makedirs(os.path.dirname(report_path) or ".", exist_ok=True)
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)
    runner.append_rollouts(args.rollouts or config.artifact_path(
        "rollouts.jsonl"), episodes)
    append_metric(args.metrics or config.artifact_path("metrics.json"),
                  0, success_rate(episodes), len(episodes),
                  phase="repro", agent=agent.agent_id)

    verdict = "REPRODUCED" if report["reproduced"] else "NOT REPRODUCED"
    print(f"{verdict}: {report['signature_failures']}/"
          f"{report['failures']} failures match the pagination signature "
          f"({sum(e['passed'] for e in episodes)}/{report['n']} passed) "
          f"-> {report_path}")
    return report


if __name__ == "__main__":
    main()
