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
from pathlib import Path

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
                   "error": res.error or ("agent execution failed" if not res.ok else None),
                   "verification": res.verification,
                   "trajectory_path": res.trajectory_path, "video_path": res.video_path,
                   "agent_metadata": res.agent_metadata}
    except asyncio.TimeoutError:
        outcome = {"actions": [], "steps": 0,
                   "error": f"timeout after {timeout}s"}
    except Exception as e:
        outcome = {"actions": [], "steps": 0, "error": str(e)}
    try:
        verdict = (checker or real_site_checker.check_transfer)(
            task, target_url, outcome)
    except Exception as exc:
        verdict = {"passed": False, "reason": f"checker error: {exc}"}
    if outcome.get("error"):
        verdict = {"passed": False, "reason": (
            verdict.get("reason", "") + f"; agent error: {outcome['error']}")}
    record = {"task_id": task["id"], "seed": None, "phase": "transfer",
              "memory_hash": hashlib.sha256(notes.encode()).hexdigest(),
              "verification": verdict,
              "agent": agent.agent_id, "passed": bool(verdict.get("passed")),
              "reason": verdict.get("reason", outcome.get("error") or ""),
              "steps": outcome["steps"], "actions": outcome["actions"]}
    for key in ("trajectory_path", "video_path", "agent_metadata"):
        if outcome.get(key):
            record[key] = outcome[key]
    return record


def load_saved_agent(path, **browser_options):
    """Restore a portable config, rejecting missing or altered run evidence."""
    from capture.agents import POLICY_VERSION, LLM_POLICY_VERSION
    from capture.llm import resolve_config

    path = Path(path).resolve()
    cfg = config.load_json(path)
    if cfg.get("schema_version") != 2:
        raise ValueError("agent config predates reproducible settings; regenerate it")
    spec_path = path.parent / cfg["spec_path"]
    if hashlib.sha256(spec_path.read_bytes()).hexdigest() != cfg["spec_hash"]:
        raise ValueError("saved spec hash does not match")
    spec = config.load_json(spec_path)
    notes = (path.parent / cfg["memory_path"]).read_text()
    if hashlib.sha256(notes.encode()).hexdigest() != cfg["memory_hash"]:
        raise ValueError("saved memory hash does not match")
    metadata = cfg["agent_metadata"]
    options = dict(browser_options)
    if cfg["agent"].startswith("browser"):
        expected_policy = LLM_POLICY_VERSION if metadata["strategy"] == "llm" else POLICY_VERSION
        if metadata.get("policy_version") != expected_policy:
            raise ValueError("saved browser policy differs; regenerate the experiment")
        options["max_steps"] = metadata["max_steps"]
        if metadata["strategy"] == "llm":
            options["model_config"] = resolve_config(
                metadata["provider"], metadata["model"], metadata["temperature"])
    agent = make_agent(cfg["agent"], spec, **options)
    if getattr(agent, "metadata", {}) != metadata:
        raise ValueError("restored agent settings do not match the saved configuration")
    return cfg, spec, cfg["task"], agent, notes


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
    ap.add_argument("--record-dir", default=None)
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--env-file", help="local credentials file, e.g. .env.local")
    args = ap.parse_args(argv)
    if args.env_file:
        from capture.llm import load_env_file
        load_env_file(args.env_file)

    options = {"artifact_dir": args.record_dir, "headed": args.headed}
    if args.config or not args.agent:
        if any((args.agent, args.task, args.tasks, args.spec, args.memory, args.target_url)):
            ap.error("saved-config transfer uses its original agent/task/spec/memory/target")
        cfg_path = Path(args.config or config.artifact_path("agent_config.json")).resolve()
        cfg, spec, task, agent, notes = load_saved_agent(cfg_path, **options)
        args.spec = str(cfg_path.parent / cfg["spec_path"])
    else:
        spec = config.load_spec(args.spec)
        tasks = config.load_tasks(args.tasks, spec)
        task = tasks[args.task or next(iter(tasks))]
        agent = make_agent(args.agent, spec, **options)
        notes = Path(args.memory).read_text() if args.memory else ""
    if not args.rehearse and not agent.agent_id.startswith("browser"):
        ap.error("real-site transfer requires a browser agent")

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
        trajectory = config.load_json(args.trajectory) if args.trajectory else {}
        target_url = args.target_url or spec.get("site_url") or trajectory.get("site_url")
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
        "checker": (os.environ.get("LOOP_REAL_CHECKER") or "capture.checker.verify"
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
    raise SystemExit(0 if main()["passed"] else 1)
