"""Continue completed learning rounds, retaining the original baseline and all transfers."""
import argparse
import asyncio
import json
from pathlib import Path

from capture.llm import resolve_config, make_llm, check_provider
from env.runtime import running_env
from . import improve, runner, transfer
from .agent_browser import BrowserAgent
from .env_client import EnvClient
from .experiment import Experiment
from .memory import MemoryStore


async def run(args, model):
    experiment = Experiment.restore(args.directory)
    out, m = experiment.directory, experiment.manifest
    if m["status"] not in ("blocked", "complete"):
        raise ValueError("only a run with completed learning rounds can be continued")
    cfg = json.loads((out / "agent_config.json").read_text())
    history = cfg["history"]
    if not history:
        raise ValueError("no completed learning rounds to continue")
    task = next(t for t in m["tasks"] if t["id"] == cfg["task_id"])
    provider = make_llm(model)
    agent = BrowserAgent("llm", m["spec"], model_config=model, llm_fn=provider,
                         max_steps=m["agent_metadata"].get("max_steps", 30),
                         artifact_dir=out / "episodes")
    if agent.metadata != m["agent_metadata"]:
        raise ValueError("continuation must preserve the original agent configuration")
    m.setdefault("continuations", []).append({
        "after_round": history[-1]["round"], "additional_rounds": args.rounds,
        "previous_status": m["status"], "previous_reason": m.get("reason"),
        "distillation": "task-conditioned verified successes",
    })
    m.update(status="running", reason=None, transfer_pending=True)
    try:
        with running_env(out / "env_spec.json", out / "component", out / "sessions") as (url, app):
            m.update(env_url=url, env_running=True)
            factory = lambda: EnvClient(url)
            experiment.stage("improve")
            memory = MemoryStore(str(out / "skill_notes.md"))
            new_history = await improve.improve(
                factory, agent, task, args.rounds, memory=memory, llm=provider,
                train_seeds=cfg["train_seeds"], eval_seeds=cfg["eval_seeds"],
                eval_overrides=cfg["eval_overrides"], strict=True, explore=args.explore,
                attempts=args.attempts, start_round=history[-1]["round"],
                rollouts_path=str(out / "rollouts.jsonl"),
                metrics_path=str(out / "metrics.json"),
                timeout=args.timeout, concurrency=args.concurrency)
            history.extend(new_history)
            improve.write_agent_config(str(out / "agent_config.json"), agent, task,
                                       memory, history, cfg["train_seeds"],
                                       cfg["eval_seeds"], cfg["eval_overrides"])
            m["summary"]["improve"] = history
            last = history[-1]
            if not memory.load() or not any(h["successes_distilled"] for h in history):
                return experiment.block("No verified training successes produced usable memory.")
            if last["eval_success_rate"] <= last["baseline_eval_success_rate"]:
                return experiment.block("Final evaluation did not improve over the original baseline.")
            experiment.stage("transfer")
            agent.headed = args.headed
            record = await transfer.transfer(
                agent, task, m["spec"]["site_url"], notes=memory.load(), timeout=args.timeout)
            record.update(mode="real", target_url=m["spec"]["site_url"],
                          checker="capture.checker.verify", round=last["round"] + 1)
            if (out / "transfer_result.json").exists():
                past = json.loads((out / "transfer_history.json").read_text()) if (out / "transfer_history.json").exists() else []
                past.append(json.loads((out / "transfer_result.json").read_text()))
                experiment.write("transfer_history.json", past)
            experiment.write("transfer_result.json", record)
            runner.append_rollouts(str(out / "rollouts.jsonl"), [record])
            m["transfer_pending"] = False
            m["summary"]["transfer"] = {"passed": record["passed"], "reason": record["reason"]}
            if not record["passed"]:
                return experiment.block("Improved memory did not transfer successfully to the real site.")
        m.update(status="complete", stage="complete",
                 reason="Observed improvement and verified real transfer; inspect counts and uncertainty.")
        return m
    except asyncio.CancelledError:
        m.update(status="interrupted", reason="Continuation cancelled; partial evidence retained.")
        raise
    except Exception as error:
        m.update(status="error", reason=str(error))
        raise
    finally:
        m["env_running"] = False
        experiment.save()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("directory")
    ap.add_argument("--rounds", type=int, default=1)
    ap.add_argument("--attempts", type=int, default=1)
    ap.add_argument("--timeout", type=int, default=360)
    ap.add_argument("--concurrency", type=int, default=1)
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--explore", action="store_true")
    args = ap.parse_args(argv)
    if min(args.rounds, args.attempts, args.timeout, args.concurrency) < 1:
        ap.error("rounds, attempts, timeout and concurrency must be positive")
    manifest = json.loads((Path(args.directory) / "run_manifest.json").read_text())
    meta = manifest["agent_metadata"]
    model = resolve_config(meta["provider"], meta["model"], meta["temperature"])
    check_provider(model)
    result = asyncio.run(run(args, model))
    print(json.dumps({"status": result["status"], "reason": result["reason"],
                      "artifacts": args.directory}, indent=2))
    return 0 if result["status"] == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
