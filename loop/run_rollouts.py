"""Rollout runner CLI.

  python -m loop.run_rollouts --agent sim:naive --task t2 --seeds 0-9
  python -m loop.run_rollouts --agent sim:smart --task t1 --seeds 0-4 \\
      --round 1 --notes-file loop/artifacts/skill_notes.md
  python -m loop.run_rollouts --agent sim:naive --task t2 --seeds 0-9 \\
      --in-process          # no server needed (tests / offline demo)

Appends {round, task_id, seed, passed, reason, steps, ...} lines to
rollouts.jsonl (default loop/artifacts/rollouts.jsonl).
"""
import argparse
import asyncio
import json
import sys

from . import config, runner
from .agent import make_agent
from .env_client import EnvClient
from .metrics import success_rate
from .seeds import parse_seeds


def _client_factory(args):
    if args.in_process:
        from env.app import create_app
        app = create_app(args.spec)
        return lambda: EnvClient.for_app(app)
    url = args.env_url or config.env_url()
    return lambda: EnvClient(url)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="loop.run_rollouts")
    ap.add_argument("--agent", default="sim:naive",
                    help="sim:naive|sim:smart|sim:auto|browser")
    ap.add_argument("--task", default="t2")
    ap.add_argument("--seeds", default="0-9")
    ap.add_argument("--round", type=int, default=0)
    ap.add_argument("--phase", default=None, help="train|eval|repro|transfer")
    ap.add_argument("--notes-file", default=None,
                    help="skill notes injected into the agent")
    ap.add_argument("--env-url", default=None)
    ap.add_argument("--in-process", action="store_true",
                    help="drive B's app in-process; no server needed")
    ap.add_argument("--tasks", default=None, help="tasks.json path")
    ap.add_argument("--spec", default=None, help="env_spec.json path")
    ap.add_argument("--out", default=None, help="rollouts.jsonl path")
    ap.add_argument("--popup", default=None, help="pin popup knob (0|1)")
    ap.add_argument("--labels", default=None, help="pin label set index")
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--metrics", default=None,
                    help="also append a summary entry to this metrics.json")
    args = ap.parse_args(argv)

    spec = config.load_spec(args.spec)
    tasks = config.load_tasks(args.tasks)
    if args.task not in tasks:
        sys.exit(f"unknown task {args.task!r} (have: {sorted(tasks)})")
    task = tasks[args.task]
    agent = make_agent(args.agent, spec)

    notes = ""
    if args.notes_file:
        notes = open(args.notes_file).read()

    overrides = {k: v for k, v in
                 {"popup": args.popup, "labels": args.labels}.items()
                 if v is not None}

    out = args.out or config.artifact_path("rollouts.jsonl")
    cache, cache_path = None, None
    if not args.no_cache:
        cache_path = config.artifact_path("baseline_cache.json")
        cache = runner.load_cache(cache_path)

    records = asyncio.run(runner.run_round(
        _client_factory(args), agent, task, parse_seeds(args.seeds),
        args.round, overrides=overrides, skill_notes=notes,
        timeout=args.timeout, concurrency=args.concurrency,
        phase=args.phase, cache=cache, cache_path=cache_path))

    runner.append_rollouts(out, records)
    rate = success_rate(records)
    for r in records:
        mark = "PASS" if r["passed"] else "FAIL"
        print(f"  seed {r['seed']:>3}  {mark}  {r['reason']}  "
              f"[{r['steps']} steps]")
    print(f"{args.agent} {args.task} round {args.round}: "
          f"{sum(r['passed'] for r in records)}/{len(records)} "
          f"({rate:.0%}) -> {out}")

    if args.metrics:
        from .metrics import append_metric
        append_metric(args.metrics, args.round, rate, len(records))


if __name__ == "__main__":
    main()
