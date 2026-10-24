"""demo.run: the whole pipeline end-to-end, in-process."""
import asyncio
import json

import pytest

from env.app import create_app
from loop import config, demo
from loop.env_client import EnvClient


@pytest.fixture()
def factory(tmp_path, monkeypatch):
    monkeypatch.setenv("ENV_SESSIONS_DIR", str(tmp_path / "sessions"))
    app = create_app()
    return lambda: EnvClient.for_app(app)


def test_full_pipeline(factory, tmp_path, monkeypatch):
    monkeypatch.setenv("LOOP_ARTIFACTS", str(tmp_path / "artifacts"))
    spec = config.load_spec()
    task = config.load_tasks()["t2"]
    summary = asyncio.run(demo.run(
        factory, spec, task, rounds=2,
        train_seeds=range(0, 8), eval_seeds=range(100, 104),
        repro_seeds=range(0, 8)))

    assert summary["repro"]["reproduced"] is True
    assert summary["repro"]["baseline_success_rate"] < 1.0
    assert summary["improve"][-1]["eval_success_rate"] == 1.0
    assert summary["transfer"]["passed"] is True

    # every contract artifact exists and is well-formed
    art = tmp_path / "artifacts"
    report = json.load(open(art / "repro_report.json"))
    assert report["signature_failures"] == report["failures"] > 0
    metrics = json.load(open(art / "metrics.json"))["metrics"]
    rates = {m["round"]: m["success_rate"] for m in metrics}
    assert rates[0] < 1.0 and rates[max(rates)] == 1.0
    rollouts = [json.loads(l) for l in open(art / "rollouts.jsonl")]
    assert {r["phase"] for r in rollouts} >= {"repro", "train", "eval",
                                             "transfer"}
    cfg = json.load(open(art / "agent_config.json"))
    assert cfg["final_eval_success_rate"] == 1.0
    assert "paginate" in (art / "skill_notes.md").read_text().lower()
