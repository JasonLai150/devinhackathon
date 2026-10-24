"""Credentialed, evidence-gated Failure-to-Env experiment.

python -m loop.experiment --preflight
python -m loop.experiment --out .integration/llm-demo --headed

No scripted substitution, cached LLM episodes, or fabricated learning curve.
"""
import argparse
import asyncio
import importlib.util
import json
import shutil
import time
from pathlib import Path

from capture.llm import resolve_config, make_llm, load_env_file
from capture.recording import artifact_ref
from . import config, improve, repro_check, runner, transfer
from .agent_browser import BrowserAgent
from .env_client import EnvClient
from .memory import MemoryStore
from .metrics import append_metric, success_rate
from .seeds import parse_seeds, EVAL_OVERRIDES


def preflight(args):
    checks, model = {}, None
    try:
        model = resolve_config(args.provider, args.model, args.temperature)
        checks["credentials"] = {"ok": True, **model.metadata()}
    except (ValueError, RuntimeError) as error:
        checks["credentials"] = {"ok": False, "reason": str(error)}
    checks["node"] = {"ok": bool(shutil.which("node")), "reason": "needed for JS syntax checks"}
    checks["browser"] = {"ok": False, "reason": "install capture requirements and Chromium"}
    if importlib.util.find_spec("playwright"):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            checks["browser"]["ok"] = Path(pw.chromium.executable_path).is_file()
    return {"ready": all(c["ok"] for c in checks.values()), "checks": checks}, model


class Experiment:
    def __init__(self, directory, agent, task, spec, mode="LLM"):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.started = time.monotonic()
        self.manifest = {
            "schema_version": 2, "mode": mode, "status": "running", "stage": "capture",
            "agent": agent.agent_id, "agent_metadata": agent.metadata,
            "spec": spec, "tasks": [task], "summary": {}, "events": [],
            "env_running": False, "trajectory_path": None,
        }
        self.save()

    def save(self):
        self.manifest["elapsed_s"] = round(time.monotonic() - self.started, 2)
        path = self.directory / "run_manifest.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.manifest, indent=2))
        temporary.replace(path)

    def stage(self, name):
        self.manifest["stage"] = name
        self.manifest["events"].append({"stage": name, "elapsed_s": round(time.monotonic() - self.started, 2)})
        self.save()
        print(f"stage: {name}", flush=True)

    def block(self, reason):
        self.manifest.update(status="blocked", reason=reason)
        self.save()
        print(f"BLOCKED: {reason}", flush=True)
        return self.manifest

    def write(self, name, value):
        path = self.directory / name
        path.write_text(json.dumps(value, indent=2))
        return path


async def run(args, model):
    from capture.localize import build_spec
    from env.generate import generate
    from env.runtime import running_env

    out = Path(args.out).resolve()
    # Never append a different experiment to old evidence.
    if out.exists() and any(p.name != "preflight.json" for p in out.iterdir()):
        raise ValueError("output directory is not empty; choose a fresh --out directory")
    spec = config.load_spec(args.spec)
    task = config.load_tasks(spec=spec)[args.task]
    if task["id"] != "shop-cheapest" or task.get("params"):
        raise ValueError("real-site experiment currently supports shop-cheapest only")
    provider = make_llm(model)
    agent = BrowserAgent("llm", spec, model_config=model, llm_fn=provider,
                         max_steps=args.max_steps, artifact_dir=out / "episodes",
                         headed=args.headed, slow_mo=args.slow_mo)
    experiment = Experiment(out, agent, task, spec)
    m = experiment.manifest
    try:
        # Genuine baseline: all attempts are retained, not just the selected failure.
        baseline = []
        for attempt in range(args.baseline_attempts):
            try:
                result = await asyncio.wait_for(
                    agent.run(None, task, {"entry_url": spec["site_url"]}), args.timeout)
                baseline.append({"attempt": attempt, "ok": result.ok, "error": result.error,
                                 "verification": result.verification,
                                 "trajectory_path": result.trajectory_path, "video_path": result.video_path})
            except asyncio.TimeoutError:
                baseline.append({"attempt": attempt, "ok": False,
                                 "error": f"timeout after {args.timeout}s"})
            experiment.write("baseline_trials.json", baseline)
        failures = [r for r in baseline if r["ok"] and r["verification"]
                    and not r["verification"]["passed"]]
        matching = [r for r in failures if r.get("trajectory_path") and
                    repro_check.real_failure(config.load_json(r["trajectory_path"]))[
                        "failure_kind"] == "pagination_incomplete"]
        m["baseline"] = {"n": len(baseline), "task_failures": len(failures),
                         "pagination_failures": len(matching),
                         "execution_errors": sum(not r["ok"] for r in baseline)}
        if len(matching) < args.minimum_failures:
            return experiment.block("No reliable pagination failure with matching browser/checker evidence.")
        m["trajectory_path"] = matching[0]["trajectory_path"]
        trajectory = config.load_json(m["trajectory_path"])
        # Domain-specific deterministic localization is disclosed in the manifest.
        experiment.stage("localize")
        spec = build_spec(trajectory)
        agent.spec = spec
        spec_path = experiment.write("env_spec.json", spec)
        m.update(spec=spec, spec_path=artifact_ref(spec_path),
                 localization="deterministic toolshop adapter")

        experiment.stage("generate")
        component = out / "component"
        report = await asyncio.to_thread(
            generate, str(spec_path), str(component), llm=provider,
            browser_check=True, allow_fallback=args.allow_verbatim)
        m["generation_report"] = report
        if report["path"] == "failed":
            return experiment.block("Environment generation or browser fidelity validation failed.")
        agent.headed = False
        with running_env(spec_path, component, out / "sessions") as (url, app):
            m.update(env_url=url, env_running=True)
            factory = lambda: EnvClient(url)
            experiment.stage("reproduce")
            episodes = await repro_check.repro(
                factory, agent, task, args.repro_seeds, spec,
                timeout=args.timeout, concurrency=args.concurrency)
            runner.append_rollouts(str(out / "rollouts.jsonl"), episodes)
            reproduction = repro_check.build_report(agent.agent_id, task, spec, episodes, trajectory)
            experiment.write("repro_report.json", reproduction)
            m["summary"]["repro"] = {"reproduced": reproduction["reproduced"],
                                      "baseline_success_rate": success_rate(episodes)}
            if episodes and episodes[0].get("trajectory_path"):
                trace = config.load_json(episodes[0]["trajectory_path"])
                if trace["steps"]:
                    m["env_screenshot"] = trace["steps"][0]["screenshot_path"]
            if not reproduction["reproduced"]:
                return experiment.block("The LLM's real-site failure did not reproduce in the sandbox.")
            append_metric(str(out / "metrics.json"), 0, success_rate(episodes),
                          len(episodes), phase="repro", agent=agent.agent_id)

            experiment.stage("improve")
            memory = MemoryStore(str(out / "skill_notes.md"))
            history = await improve.improve(
                factory, agent, task, args.rounds, memory=memory, llm=provider,
                train_seeds=args.train_seeds, eval_seeds=args.eval_seeds,
                eval_overrides=EVAL_OVERRIDES, strict=True, attempts=args.attempts,
                rollouts_path=str(out / "rollouts.jsonl"),
                metrics_path=str(out / "metrics.json"), timeout=args.timeout,
                concurrency=args.concurrency)
            improve.write_agent_config(str(out / "agent_config.json"), agent, task,
                                       memory, history, args.train_seeds,
                                       args.eval_seeds, EVAL_OVERRIDES)
            m["summary"]["improve"] = history
            last = history[-1]
            if not memory.load() or not any(h["successes_distilled"] for h in history):
                return experiment.block("No verified training successes produced usable memory.")
            if last["eval_success_rate"] <= last["baseline_eval_success_rate"]:
                return experiment.block("Final held-out evaluation did not improve; the measured curve is retained.")

            experiment.stage("transfer")
            agent.headed = args.headed
            record = await transfer.transfer(
                agent, task, spec["site_url"], notes=memory.load(), timeout=args.timeout)
            record.update(mode="real", target_url=spec["site_url"],
                          checker="capture.checker.verify", round=args.rounds + 1)
            experiment.write("transfer_result.json", record)
            runner.append_rollouts(str(out / "rollouts.jsonl"), [record])
            m["summary"]["transfer"] = {"passed": record["passed"], "reason": record["reason"]}
            if not record["passed"]:
                return experiment.block("Improved memory did not transfer successfully to the real site.")
        m.update(status="complete", stage="complete",
                 reason="Observed improvement and verified real transfer; inspect counts and uncertainty.")
        return m
    except asyncio.CancelledError:
        m.update(status="interrupted", reason="Experiment cancelled; partial evidence retained.")
        raise
    except Exception as error:
        m.update(status="error", reason=str(error))
        raise
    finally:
        m["env_running"] = False
        experiment.save()


def parser():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=".integration/llm-demo")
    ap.add_argument("--spec", default="fixtures/env_spec.json")
    ap.add_argument("--task", default="shop-cheapest")
    ap.add_argument("--provider", choices=("gemini", "anthropic", "openai"))
    ap.add_argument("--env-file", help="local credentials file, e.g. .env.local")
    ap.add_argument("--model")
    ap.add_argument("--temperature", type=float, default=0.7)
    ap.add_argument("--max-steps", type=int, default=30)
    ap.add_argument("--baseline-attempts", type=int, default=3)
    ap.add_argument("--minimum-failures", type=int, default=2)
    ap.add_argument("--repro-seeds", default="0-2")
    ap.add_argument("--train-seeds", default="0-4")
    ap.add_argument("--eval-seeds", default="100-109")
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--attempts", type=int, default=2, help="independent attempts per training seed")
    ap.add_argument("--concurrency", type=int, default=2)
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--headed", action="store_true", help="show real baseline/transfer in a browser window")
    ap.add_argument("--slow-mo", type=int, default=0)
    ap.add_argument("--allow-verbatim", action="store_true", help="explicitly allow the reference fallback")
    ap.add_argument("--preflight", action="store_true", help="check dependencies without making model calls")
    return ap


def main(argv=None):
    ap = parser()
    args = ap.parse_args(argv)
    if args.env_file:
        load_env_file(args.env_file)
    for field in ("repro_seeds", "train_seeds", "eval_seeds"):
        setattr(args, field, parse_seeds(getattr(args, field)))
    if (not args.train_seeds or not args.eval_seeds or not args.repro_seeds or
            (set(args.train_seeds) | set(args.repro_seeds)) & set(args.eval_seeds)):
        ap.error("evaluation seeds must be nonempty and disjoint from training/repro")
    if any(len(set(getattr(args, k))) != len(getattr(args, k))
           for k in ("repro_seeds", "train_seeds", "eval_seeds")):
        ap.error("seed sets must not contain duplicates")
    if not 1 <= args.minimum_failures <= args.baseline_attempts:
        ap.error("minimum failures must be between 1 and baseline attempts")
    if min(args.rounds, args.attempts, args.concurrency, args.timeout, args.max_steps) < 1:
        ap.error("rounds, attempts, concurrency, timeout and max steps must be positive")
    checks, model = preflight(args)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "preflight.json").write_text(json.dumps(checks, indent=2))
    if args.preflight or not checks["ready"]:
        print(json.dumps(checks, indent=2))
        return 0 if checks["ready"] else 2
    manifest = asyncio.run(run(args, model))
    print(json.dumps({"status": manifest["status"], "reason": manifest["reason"],
                      "artifacts": str(out)}, indent=2))
    return 0 if manifest["status"] == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
