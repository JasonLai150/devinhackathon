"""Improvement loop — rejection sampling into skill-note memory.

Each round: train episodes on TRAIN_SEEDS (improvement may ONLY see these)
-> distill verified successes into skill_notes.md -> eval on held-out
EVAL_SEEDS + pinned perturbation overrides -> append metrics.json.
Round 0 is the baseline repro; rounds 1..R should climb.

  python -m loop.improve --agent sim:auto --task t2 --rounds 3 --in-process

Writes agent_config.json — the "improved agent config" deliverable the
transfer run loads.
"""
import argparse
import asyncio
import json
import os
import hashlib
from pathlib import Path

from . import config, runner
from .agent import make_agent
from .distill import distill, distill_verified
from .llm import default_llm
from .memory import MemoryStore
from .metrics import append_metric, success_rate, wilson_interval
from .run_rollouts import _client_factory
from .seeds import (EVAL_OVERRIDES, EVAL_SEEDS, TRAIN_OVERRIDES, TRAIN_SEEDS,
                    parse_seeds)


async def improve(client_factory, agent, task, rounds,
                  memory=None, llm="__default__",
                  train_seeds=TRAIN_SEEDS, eval_seeds=EVAL_SEEDS,
                  train_overrides=TRAIN_OVERRIDES,
                  eval_overrides=EVAL_OVERRIDES,
                  rollouts_path=None, metrics_path=None,
                  cache=None, cache_path=None,
                  timeout=120, concurrency=8, strict=False, attempts=1):
    train_seeds, eval_seeds = list(train_seeds), list(eval_seeds)
    if not train_seeds or not eval_seeds or set(train_seeds) & set(eval_seeds):
        raise ValueError("training/evaluation seeds must be nonempty and disjoint")
    if len(set(train_seeds)) != len(train_seeds) or len(set(eval_seeds)) != len(eval_seeds):
        raise ValueError("seed sets must not contain duplicates")
    if rounds < 1 or attempts < 1:
        raise ValueError("rounds and attempts must be positive")
    if llm == "__default__":
        llm = default_llm()
    if strict and llm is None:
        raise RuntimeError("strict improvement requires an LLM distiller")
    memory = memory or MemoryStore(config.artifact_path("skill_notes.md"))
    notes = memory.load()
    history = []
    evaluation = {"task": task, "seeds": eval_seeds, "overrides": eval_overrides,
                  "spec": getattr(agent, "spec", {}), "agent": getattr(agent, "metadata", {})}
    evaluation_id = hashlib.sha256(json.dumps(evaluation, sort_keys=True).encode()).hexdigest()
    snapshots = Path(memory.path).parent / "rounds"
    snapshots.mkdir(parents=True, exist_ok=True)

    async def evaluate(round_no):
        # No reuse: this is a new measurement even when memory did not change.
        records = await runner.run_round(
            client_factory, agent, task, eval_seeds, round_no,
            overrides=eval_overrides, skill_notes=notes, phase="eval",
            timeout=timeout, concurrency=concurrency)
        if rollouts_path:
            runner.append_rollouts(rollouts_path, records)
        passed = sum(r["passed"] for r in records)
        if metrics_path:
            append_metric(metrics_path, round_no, success_rate(records), len(records),
                          phase="eval", agent=agent.agent_id, passed=passed,
                          ci95=wilson_interval(passed, len(records)),
                          evaluation_id=evaluation_id, seeds=eval_seeds,
                          overrides=eval_overrides, memory_hash=memory.hash())
        return records

    (snapshots / "round-0.md").write_text(notes)
    baseline = await evaluate(0)
    baseline_rate = success_rate(baseline)

    for r in range(1, rounds + 1):
        train_recs = []
        for attempt in range(attempts):
            batch = await runner.run_round(
                client_factory, agent, task, train_seeds, r,
                overrides=train_overrides, skill_notes=notes, phase="train",
                cache=None if strict or attempts > 1 else cache,
                cache_path=None if strict or attempts > 1 else cache_path,
                timeout=timeout, concurrency=concurrency)
            train_recs.extend({**record, "attempt": attempt} for record in batch)
        if rollouts_path:
            runner.append_rollouts(rollouts_path, train_recs)

        successes = [x for x in train_recs if x["passed"]]
        failures = [x for x in train_recs if not x["passed"]]
        new_notes = (distill_verified(notes, successes, llm) if strict
                     else distill(notes, successes, llm, failures=failures))
        notes_changed = new_notes != notes
        if notes_changed:
            memory.save(new_notes)
            notes = new_notes

        (snapshots / f"round-{r}.md").write_text(notes)
        evidence = {"round": r, "mode": "verified-success-LLM" if strict else "rehearsal",
                    "source_episode_ids": [x.get("episode_id") for x in successes],
                    "training_seeds": train_seeds, "attempts_per_seed": attempts,
                    "successes": len(successes), "notes_changed": notes_changed}
        (snapshots / f"round-{r}.json").write_text(json.dumps(evidence, indent=2))
        eval_recs = await evaluate(r)

        rate = success_rate(eval_recs)
        history.append({
            "round": r,
            "train_success_rate": success_rate(train_recs),
            "eval_success_rate": rate,
            "n_eval": len(eval_recs),
            "successes_distilled": len(successes),
            "notes_changed": notes_changed,
            "baseline_eval_success_rate": baseline_rate,
            "evaluation_id": evaluation_id,
            "distillation_mode": "verified-success-LLM" if strict else "rehearsal",
        })
    return history


def write_agent_config(path, agent, task, memory, history, train_seeds,
                       eval_seeds, eval_overrides):
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    memory.save(memory.load())
    spec_path = path.with_suffix(".spec.json")
    spec_path.write_text(json.dumps(agent.spec, indent=2))
    cfg = {
        "schema_version": 2,
        "agent": agent.agent_id,
        "agent_metadata": getattr(agent, "metadata", {}),
        "task_id": task["id"],
        "task": task,
        "spec_path": spec_path.name,
        "spec_hash": hashlib.sha256(spec_path.read_bytes()).hexdigest(),
        "rounds": len(history),
        "memory_path": os.path.relpath(Path(memory.path).resolve(), path.parent),
        "memory_hash": hashlib.sha256(memory.load().encode()).hexdigest(),
        "train_seeds": list(train_seeds),
        "eval_seeds": list(eval_seeds),
        "eval_overrides": eval_overrides,
        "final_eval_success_rate": (history[-1]["eval_success_rate"]
                                    if history else None),
        "history": history,
    }
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(cfg, f, indent=2)
    return cfg


def main(argv=None):
    ap = argparse.ArgumentParser(prog="loop.improve")
    ap.add_argument("--agent", default="sim:auto")
    ap.add_argument("--task", default="t2")
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--env-url", default=None)
    ap.add_argument("--in-process", action="store_true")
    ap.add_argument("--tasks", default=None)
    ap.add_argument("--spec", default=None)
    ap.add_argument("--train-seeds", default=None)
    ap.add_argument("--eval-seeds", default=None)
    ap.add_argument("--memory", default=None, help="skill_notes.md path")
    ap.add_argument("--rollouts", default=None)
    ap.add_argument("--metrics", default=None)
    ap.add_argument("--config-out", default=None,
                    help="agent_config.json path")
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args(argv)

    spec = config.load_spec(args.spec)
    task = config.load_tasks(args.tasks, spec)[args.task]
    agent = make_agent(args.agent, spec)
    memory = MemoryStore(args.memory or
                         config.artifact_path("skill_notes.md"))
    cache, cache_path = None, None
    if not args.no_cache:
        cache_path = config.artifact_path("baseline_cache.json")
        cache = runner.load_cache(cache_path)

    train_seeds = parse_seeds(args.train_seeds) if args.train_seeds \
        else TRAIN_SEEDS
    eval_seeds = parse_seeds(args.eval_seeds) if args.eval_seeds \
        else EVAL_SEEDS

    history = asyncio.run(improve(
        _client_factory(args), agent, task, args.rounds,
        memory=memory, train_seeds=train_seeds, eval_seeds=eval_seeds,
        rollouts_path=args.rollouts or config.artifact_path("rollouts.jsonl"),
        metrics_path=args.metrics or config.artifact_path("metrics.json"),
        cache=cache, cache_path=cache_path, timeout=args.timeout,
        concurrency=args.concurrency))

    cfg_path = args.config_out or config.artifact_path("agent_config.json")
    write_agent_config(cfg_path, agent, task, memory, history,
                       train_seeds, eval_seeds, EVAL_OVERRIDES)
    for h in history:
        print(f"round {h['round']}: train {h['train_success_rate']:.0%} "
              f"-> eval {h['eval_success_rate']:.0%} "
              f"({'notes updated' if h['notes_changed'] else 'notes same'})")
    print(f"agent config -> {cfg_path}")


if __name__ == "__main__":
    main()
