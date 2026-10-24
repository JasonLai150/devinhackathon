"""Record the connected MVP with the scripted browser strategy.

python -m loop.rehearse --out .integration/rehearsal --real-transfer

Captures a fresh real failure, validates the supplied component in Chromium,
starts an isolated sandbox, runs the loop and writes matched dashboard evidence.
This command never calls an LLM and never labels its memory switch as learning.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path

from capture.localize import build_spec
from capture.recording import artifact_ref
from env.generate import generate
from env.runtime import running_env
from . import config, demo, repro_check
from .agent_browser import BrowserAgent
from .env_client import EnvClient
from .experiment import Experiment
from .seeds import parse_seeds


async def run(args):
    out = Path(args.out).resolve()
    if not out.is_relative_to(Path(config._REPO)):
        raise ValueError("output must be inside the repository for dashboard replay")
    if out.exists() and any(out.iterdir()):
        raise ValueError("choose a fresh --out directory; existing recordings are preserved")
    spec = config.load_spec("fixtures/env_spec.json")
    task = config.load_tasks(spec=spec)["shop-cheapest"]
    agent = BrowserAgent("auto", spec, artifact_dir=out / "episodes", headed=args.headed)
    experiment = Experiment(out, agent, task, spec, mode="scripted")
    manifest = experiment.manifest
    old_artifacts = os.environ.get("LOOP_ARTIFACTS")
    os.environ["LOOP_ARTIFACTS"] = str(out)
    try:
        experiment.stage("capture")
        result = await asyncio.wait_for(
            agent.run(None, task, {"entry_url": spec["site_url"]}), args.timeout)
        if not result.ok or not result.trajectory_path:
            return experiment.block(f"Baseline browser failed to execute: {result.error}")
        manifest["trajectory_path"] = result.trajectory_path
        trajectory = config.load_json(result.trajectory_path)
        if repro_check.real_failure(trajectory)["failure_kind"] != "pagination_incomplete":
            return experiment.block("The baseline did not demonstrate the intended pagination failure.")

        experiment.stage("localize")
        spec = build_spec(trajectory)
        spec_path = experiment.write("env_spec.json", spec)
        manifest.update(spec=spec, spec_path=artifact_ref(spec_path),
                        localization="deterministic toolshop adapter")
        experiment.stage("generate")
        report = await asyncio.to_thread(
            generate, str(spec_path), str(out / "component"),
            llm=None, verbatim=True, browser_check=True)
        manifest["generation_report"] = report
        if report["path"] == "failed":
            return experiment.block("Reference component failed browser validation.")

        with running_env(spec_path, out / "component") as (url, app):
            manifest.update(env_url=url, env_running=True)
            manifest["summary"] = await demo.run(
                lambda: EnvClient(url), spec, task, rounds=args.rounds,
                train_seeds=args.train_seeds, eval_seeds=args.eval_seeds,
                repro_seeds=args.repro_seeds, agent_name="browser:auto",
                real_transfer=args.real_transfer, trajectory=trajectory,
                timeout=args.timeout, concurrency=args.concurrency,
                browser_options={"artifact_dir": out / "episodes"},
                stage_callback=experiment.stage)
        episodes = config.load_json(out / "repro_report.json")["episodes"]
        trace = config.load_json(episodes[0]["trajectory_path"])
        manifest["env_screenshot"] = trace["steps"][0]["screenshot_path"]
        summary = manifest["summary"]
        if not summary["repro"]["reproduced"] or not summary["transfer"]["passed"]:
            return experiment.block("Reproduction or transfer failed; evidence retained.")
        manifest.update(status="complete", stage="complete",
                        reason="Recorded scripted rehearsal; no LLM learning claim.")
        return manifest
    except asyncio.CancelledError:
        manifest.update(status="interrupted", reason="Rehearsal cancelled; partial evidence retained.")
        raise
    except Exception as exc:
        manifest.update(status="error", reason=str(exc))
        raise
    finally:
        manifest["env_running"] = False
        experiment.save()
        if old_artifacts is None:
            os.environ.pop("LOOP_ARTIFACTS", None)
        else:
            os.environ["LOOP_ARTIFACTS"] = old_artifacts


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=".integration/rehearsal")
    ap.add_argument("--real-transfer", action="store_true")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--rounds", type=int, default=1)
    ap.add_argument("--train-seeds", default="0-2")
    ap.add_argument("--eval-seeds", default="100-102")
    ap.add_argument("--repro-seeds", default="0-2")
    ap.add_argument("--concurrency", type=int, default=2)
    ap.add_argument("--timeout", type=int, default=300)
    args = ap.parse_args(argv)
    for name in ("train_seeds", "eval_seeds", "repro_seeds"):
        setattr(args, name, parse_seeds(getattr(args, name)))
    if min(args.rounds, args.concurrency, args.timeout) < 1:
        ap.error("rounds, concurrency and timeout must be positive")
    if (not args.eval_seeds or not args.train_seeds or not args.repro_seeds or
            (set(args.train_seeds) | set(args.repro_seeds)) & set(args.eval_seeds)):
        ap.error("evaluation seeds must be nonempty and disjoint from train/repro")
    result = asyncio.run(run(args))
    print(json.dumps({"status": result["status"], "reason": result["reason"]}, indent=2))
    return 0 if result["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
