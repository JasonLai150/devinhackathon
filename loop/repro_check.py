"""Repro check (round 0) — does the baseline agent fail in the mini-env the
same way it failed on the real site?

Requires a verified wrong choice, page-one-only browsing, and an unseen
correct choice. Compare that evidence with the real trajectory's listing
observations and structured checker verdict. Missing real evidence permits
only a sandbox diagnostic, never a confirmed real-site reproduction.

Emits repro_report.json (the dashboard's credibility artifact), round-0
rollouts records, and a round-0 metrics.json entry.

  python -m loop.repro_check --agent sim:naive --task t2 --seeds 0-9 \\
      --in-process
"""
import argparse
import asyncio
import json
import os
from urllib.parse import parse_qs, urlsplit

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

    requests = [urlsplit(row["path"]) for row in tables.get("request_log", [])
                if urlsplit(row["path"]).path.endswith("/" + api)]
    queries = [parse_qs(url.query) for url in requests]
    pages = [int(q.get("page", ["1"])[0]) for q in queries]
    max_page = max(pages) if pages else 0

    mut = spec["routes"]["mutation"]
    bookings = tables.get(mut["table"], [])
    fk = mut["fk_column"]
    booked_id = bookings[-1][fk] if bookings else None
    entity = call["table"]
    visible_count = max([int(q.get("per_page", [per_page])[0]) for q in queries],
                        default=per_page)
    page1_ids = {r["id"] for r in
                 sorted(tables[entity], key=lambda r: r["id"])[:visible_count]}
    verdict = record.get("verification", {})
    expected_ids = set(verdict.get("expected_ids", []))

    signals = {
        "max_listing_page_seen": max_page,
        "never_paginated": max_page < 2,
        "booked_from_page1": booked_id in page1_ids if booked_id else False,
    }
    signature_failure = (
        not record["passed"]
        and not record.get("reason", "").startswith("error:")
        and verdict.get("code") == "wrong_choice"
        and len(bookings) == 1 and bookings[0].get("quantity", 1) == 1
        and bool(expected_ids) and not expected_ids.intersection(page1_ids)
        and max_page == 1 and booked_id in page1_ids)
    return {**signals, "signature_failure": signature_failure,
            "failure_kind": "pagination_incomplete" if signature_failure else None}


def real_failure(trajectory):
    """Classify real browser evidence without inferring causes from prose."""
    trajectory = trajectory or {}
    verdict = trajectory.get("checker") or {}
    listings = [s["listing"] for s in trajectory.get("steps", [])
                if s.get("listing", {}).get("products")]
    pages = sorted({listing["page"] for listing in listings})
    products = [p for listing in listings for p in listing["products"]]
    cart = verdict.get("cart", [])
    expected = verdict.get("expected", {})
    seen_names = {p["name"].strip().lower() for p in products}
    matches = (
        trajectory.get("outcome") == "failed" and not trajectory.get("error")
        and verdict.get("passed") is False and verdict.get("code") == "wrong_choice"
        and pages == [1] and len(cart) == 1 and cart[0].get("quantity") == 1
        and cart[0].get("price") is not None
        and cart[0]["name"].strip().lower() in seen_names
        and bool(expected.get("name"))
        and expected["name"].strip().lower() not in seen_names)
    return {"failure_kind": "pagination_incomplete" if matches else None,
            "listing_pages_seen": pages,
            "reason": ("verified wrong page-one choice with correct item unseen" if matches
                       else "missing or non-matching real-site failure evidence")}


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
    real = real_failure(trajectory)
    real["site_matches"] = bool(trajectory) and trajectory.get("site_url") == spec.get("site_url")
    sandbox_match = bool(sig) and len(sig) == len(failures)
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
        "real_failure": real,
        "sandbox_reproduced": sandbox_match,
        "reproduced": (sandbox_match and real["site_matches"]
                       and real["failure_kind"] == "pagination_incomplete"),
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
