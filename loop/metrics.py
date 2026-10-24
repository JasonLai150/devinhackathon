"""metrics.json — the C -> dashboard contract.

{"metrics": [{"round": 0, "success_rate": 0.1, "n": 10}, ...]}
"""
import json
import os


def load_metrics(path):
    if not os.path.exists(path):
        return {"metrics": []}
    with open(path) as f:
        return json.load(f)


def append_metric(path, round_no, success_rate, n, **extra):
    data = load_metrics(path)
    entry = {"round": round_no, "success_rate": round(success_rate, 4),
             "n": n, **extra}
    data["metrics"].append(entry)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    return entry


def success_rate(records):
    if not records:
        return 0.0
    return sum(1 for r in records if r["passed"]) / len(records)
