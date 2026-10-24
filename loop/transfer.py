"""Transfer run — the demo's green ending.

Loads the improved agent config + distilled skill notes, runs the original
task on the real site (spec.site_url / trajectory.site_url), and judges it
with A's real-site checker (LOOP_REAL_CHECKER). Emits transfer_result.json
plus final rollouts/metrics entries.

  python -m loop.transfer --config loop/artifacts/agent_config.json
  python -m loop.transfer --rehearse --agent sim:smart --in-process

--rehearse swaps the real site for a fresh env session and the checker for
env /verify — exercises the whole transfer pipeline (and gives the demo a
deterministic green fallback) before A's site + checker exist.
"""
import argparse
import asyncio
import hashlib
import json
import os

from . import config, runner, real_site_checker
from .agent import make_agent
from .metrics import append_metric, load_metrics
from .run_rollouts import _client_factory


async def transfer(agent, task, target_url, notes="", timeout=180,
                   checker=None, env=None, session_id="transfer"):
    """Run the improved agent on the target; judge with the checker."""
    session = {"session_id": session_id, "entry_url": target_url}
    try:
        res = await asyncio.wait_for(
            agent.run(env, task, session, skill_notes=notes), timeout)
        outcome = {"actions": res.actions, "steps": res.steps,
                   "error": res.error, "verification": res.verification}
    except asyncio.TimeoutError:
        outcome = {"actions": [], "steps": 0,
                   "error": f"timeout after {timeout}s"}
    except Exception as e:
        outcome = {"actions": [], "steps": 0, "error": str(e)}
    verdict = (checker or real_site_checker.check_transfer)(
        task, target_url, outcome)
    if outcome.get("error"):
        verdict = {"passed": False, "reason": (
            verdict.get("reason", "") + f"; agent error: {outcome['error']}")}
    record = {"task_id": task["id"], "seed": None, "phase": "transfer",
              "agent": agent.agent_id, "passed": bool(verdict.get("passed")),
              "reason": verdict.get("reason", outcome.get("error") or ""),
              "steps": outcome["steps"], "actions": outcome["actions"]}
    return record


def _next_round(metrics_path):
    metrics = load_metrics(metrics_path)["metrics"]
    nums = [m["round"] for m in metrics if isinstance(m.get("round"), int)]
    return max(nums, default=0) + 1


def main(argv=None):
    ap = argparse.ArgumentParser(prog="loop.transfer")
    ap.add_argument("--config", default=None,
                    help="agent_config.json from loop.improve")
    ap.add_argument("--agent", default=None,
                    help="override config agent (sim:*|browser)")
    ap.add_argument("--task", default=None)
    ap.add_argument("--target-url", default=None,
                    help="default: site_url from trajectory, else spec")
    ap.add_argument("--memory", default=None)
    ap.add_argument("--rehearse", action="store_true",
                    help="run on a fresh env session, judged by env verify")
    ap.add_argument("--env-url", default=None)
    ap.add_argument("--in-process", action="store_true")
    ap.add_argument("--tasks", default=None)
    ap.add_argument("--spec", default=None)
    ap.add_argument("--trajectory", default=None)
    ap.add_argument("--rollouts", default=None)
    ap.add_argument("--metrics", default=None)
    ap.add_argument("--out", default=None, help="transfer_result.json path")
    ap.add_argument("--timeout", type=int, default=180)
    args = ap.parse_args(argv)

    cfg = {}
    cfg_path = args.config or config.artifact_path("agent_config.json")
    if os.path.exists(cfg_path):
        cfg = json.load(open(cfg_path))

    spec = config.load_spec(args.spec)
    tasks = config.load_tasks(args.tasks, spec)
    task = tasks[args.task or cfg.get("task_id") or "t2"]
    agent = make_agent(args.agent or cfg.get("agent") or "sim:smart", spec)

    notes = ""
    mem_path = args.memory or cfg.get("memory_path")
    if mem_path and os.path.exists(mem_path):
        notes = open(mem_path).read()

    client_factory = _client_factory(args)
    env_client = client_factory()

    if args.rehearse:
        # Fresh unseen session + env verifier = honest plumbing rehearsal.
        session = env_client.reset()
        target_url = session["entry_url"]
        sid = session["session_id"]

        def checker(task_, url, outcome):
            return env_client.verify(task_["id"], sid)
    else:
        traj_path = args.trajectory or config.trajectory_path()
        trajectory = (config.load_json(traj_path)
                      if os.path.exists(traj_path) else {})
        target_url = (args.target_url or trajectory.get("site_url")
                      or spec.get("site_url"))
        checker = None  # LOOP_REAL_CHECKER / stub

    record = asyncio.run(transfer(
        agent, task, target_url, notes=notes, timeout=args.timeout,
        checker=checker, env=env_client if args.rehearse else None,
        session_id=sid if args.rehearse else "transfer"))

    metrics_path = args.metrics or config.artifact_path("metrics.json")
    record["round"] = _next_round(metrics_path)
    rollouts_path = args.rollouts or config.artifact_path("rollouts.jsonl")
    runner.append_rollouts(rollouts_path, [record])
    append_metric(metrics_path, record["round"],
                  1.0 if record["passed"] else 0.0, 1,
                  phase="transfer", agent=agent.agent_id)

    result = {
        **record,
        "target_url": target_url,
        "mode": "rehearse" if args.rehearse else "real",
        "checker": (os.environ.get("LOOP_REAL_CHECKER")
                    if not args.rehearse else "env:/verify"),
        "notes_hash": hashlib.sha1(notes.encode()).hexdigest()[:8]
                      if notes else None,
    }
    out = args.out or config.artifact_path("transfer_result.json")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w") as f:
        json.dump(result, f, indent=2)

    mark = "PASS" if record["passed"] else "FAIL"
    print(f"transfer ({result['mode']}): {mark}  {record['reason']} "
          f"[{record['steps']} steps] -> {out}")
    return result


if __name__ == "__main__":
    main()
