"""End-to-end pipeline in one command — also the deterministic fallback.

repro (round 0, baseline fails the same way) -> improve (rounds 1..R,
metrics climb on held-out seeds) -> transfer --rehearse (improved agent
goes green). With sim:* agents this needs no LLM key, no browser, and no
server (--in-process), so it can regenerate the committed artifacts that
back the pre-recorded demo.

  python -m loop.demo --in-process
"""
import argparse
import asyncio
import json
import os

from . import config, improve, repro_check, runner, transfer
from .agent import make_agent
from .memory import MemoryStore
from .metrics import append_metric, success_rate
from .run_rollouts import _client_factory
from .seeds import (EVAL_OVERRIDES, EVAL_SEEDS, TRAIN_OVERRIDES, TRAIN_SEEDS,
                    parse_seeds)


async def run(client_factory, spec, task, rounds=2,
              train_seeds=TRAIN_SEEDS, eval_seeds=EVAL_SEEDS,
              repro_seeds=None, timeout=120, agent_name="sim:auto",
              real_transfer=False, trajectory=None, browser_options=None):
    rollouts_path = config.artifact_path("rollouts.jsonl")
    metrics_path = config.artifact_path("metrics.json")
    memory = MemoryStore(config.artifact_path("skill_notes.md"))
    cache_path = config.artifact_path("baseline_cache.json")
    cache = runner.load_cache(cache_path)
    summary = {}

    # 1. repro — baseline agent, round 0.
    naive = make_agent("sim:naive" if agent_name == "sim:auto" else agent_name,
                       spec, **(browser_options or {}))
    episodes = await repro_check.repro(
        client_factory, naive, task, repro_seeds or train_seeds[:10], spec,
        cache=cache, cache_path=cache_path, timeout=timeout)
    report = repro_check.build_report(naive.agent_id, task, spec, episodes,
                                      trajectory)
    with open(config.artifact_path("repro_report.json"), "w") as f:
        json.dump(report, f, indent=2)
    runner.append_rollouts(rollouts_path, episodes)
    append_metric(metrics_path, 0, success_rate(episodes), len(episodes),
                  phase="repro", agent=naive.agent_id)
    summary["repro"] = {
        "reproduced": report["reproduced"],
        "baseline_success_rate": success_rate(episodes)}

    # 2. improve — rounds 1..R on held-out eval seeds.
    agent = make_agent(agent_name, spec, **(browser_options or {}))
    history = await improve.improve(
        client_factory, agent, task, rounds, memory=memory,
        train_seeds=train_seeds, eval_seeds=eval_seeds,
        train_overrides=TRAIN_OVERRIDES, eval_overrides=EVAL_OVERRIDES,
        rollouts_path=rollouts_path, metrics_path=metrics_path,
        cache=cache, cache_path=cache_path, timeout=timeout)
    improve.write_agent_config(
        config.artifact_path("agent_config.json"), agent, task, memory,
        history, train_seeds, eval_seeds, EVAL_OVERRIDES)
    summary["improve"] = history

    # 3. transfer rehearse — improved agent + notes on a fresh session.
    if real_transfer:
        if not agent_name.startswith("browser"):
            raise ValueError("real transfer requires a browser agent")
        env, checker = None, None
        session = {"session_id": "transfer", "entry_url": spec["site_url"]}
    else:
        env = client_factory()
        session = env.reset()
        def checker(task_, url, outcome):
            return env.verify(task_["id"], session["session_id"])

    rec = await transfer.transfer(
        agent, task, session["entry_url"], notes=memory.load(),
        checker=checker, env=env, session_id=session["session_id"], timeout=timeout)
    from .transfer import _next_round
    rec["round"] = _next_round(metrics_path)
    runner.append_rollouts(rollouts_path, [rec])
    append_metric(metrics_path, rec["round"],
                  1.0 if rec["passed"] else 0.0, 1,
                  phase="transfer", agent=agent.agent_id)
    with open(config.artifact_path("transfer_result.json"), "w") as f:
        json.dump({**rec, "target_url": session["entry_url"],
                   "mode": "real" if real_transfer else "rehearse",
                   "checker": "capture.checker.verify" if real_transfer else "env:/verify"},
                  f, indent=2)
    summary["transfer"] = {"passed": rec["passed"], "reason": rec["reason"]}
    return summary


def main(argv=None):
    ap = argparse.ArgumentParser(prog="loop.demo")
    ap.add_argument("--task", default="t2")
    ap.add_argument("--agent", default="sim:auto")
    ap.add_argument("--real-transfer", action="store_true")
    ap.add_argument("--trajectory", default=None)
    ap.add_argument("--generate-report", default=None)
    ap.add_argument("--env-screenshot", default=None)
    ap.add_argument("--record-dir", default=None, help="record browser episode screenshots, DOM and video")
    ap.add_argument("--headed", action="store_true", help="show browser windows during rehearsal")
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--repro-seeds", default="0-9")
    ap.add_argument("--train-seeds", default=None)
    ap.add_argument("--eval-seeds", default=None)
    ap.add_argument("--env-url", default=None)
    ap.add_argument("--in-process", action="store_true")
    ap.add_argument("--tasks", default=None)
    ap.add_argument("--spec", default=None)
    ap.add_argument("--clean", action="store_true",
                    help="wipe existing artifacts first")
    args = ap.parse_args(argv)
    if args.agent in ("browser", "browser:llm"):
        ap.error("use loop.experiment for LLM runs with evidence gates; loop.demo is a scripted rehearsal")

    spec = config.load_spec(args.spec)
    task = config.load_tasks(args.tasks, spec)[args.task]

    if args.real_transfer and (args.in_process or not args.agent.startswith("browser")):
        ap.error("--real-transfer requires a browser agent and an HTTP env")
    if args.clean:
        for name in ("rollouts.jsonl", "metrics.json", "repro_report.json",
                     "skill_notes.md", "agent_config.json",
                     "transfer_result.json", "baseline_cache.json", "run_manifest.json"):
            p = config.artifact_path(name)
            if os.path.exists(p):
                os.remove(p)

    trajectory_path = args.trajectory or config.trajectory_path()
    trajectory = config.load_json(trajectory_path) if os.path.isfile(trajectory_path) else None
    summary = asyncio.run(run(
        _client_factory(args), spec, task, rounds=args.rounds,
        train_seeds=parse_seeds(args.train_seeds) if args.train_seeds
        else TRAIN_SEEDS,
        eval_seeds=parse_seeds(args.eval_seeds) if args.eval_seeds
        else EVAL_SEEDS,
        repro_seeds=parse_seeds(args.repro_seeds), agent_name=args.agent,
        real_transfer=args.real_transfer, trajectory=trajectory,
        browser_options={"artifact_dir": args.record_dir, "headed": args.headed}))
    with open(config.artifact_path("run_manifest.json"), "w") as f:
        json.dump({"spec_path": os.path.relpath(args.spec or config.spec_path(), config._REPO),
                   "spec": spec,
                   "trajectory_path": os.path.relpath(trajectory_path, config._REPO),
                   "tasks": list(config.load_tasks(args.tasks, spec).values()),
                   "agent": args.agent, "env_url": args.env_url or config.env_url(),
                   "generation_report": (config.load_json(args.generate_report)
                                         if args.generate_report else None),
                   "env_screenshot": args.env_screenshot,
                   "mode": "LLM" if args.agent in ("browser", "browser:llm") else "scripted",
                   "summary": summary}, f, indent=2)

    rep = summary["repro"]
    print(f"repro: {'REPRODUCED' if rep['reproduced'] else 'not reproduced'} "
          f"(baseline {rep['baseline_success_rate']:.0%})")
    for h in summary["improve"]:
        print(f"round {h['round']}: eval {h['eval_success_rate']:.0%}")
    t = summary["transfer"]
    print(f"transfer: {'PASS' if t['passed'] else 'FAIL'}  {t['reason']}")
    if not rep["reproduced"] or not t["passed"] or not summary["improve"] or \
            summary["improve"][-1]["eval_success_rate"] < 1:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
