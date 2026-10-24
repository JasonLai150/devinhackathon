"""Scripted rollout simulator — exercises the exact contract C's runner uses.

Two strategies:
  naive: reads only page 1 of results and books the cheapest visible
         -> reproduces the pagination failure mode (should mostly FAIL)
  smart: paginates through every page before comparing
         -> the fixed behavior (should PASS)

Not a real agent — no LLM, drives the HTTP contract directly via the Flask
test client (in-process, no server needed). Gives C a runnable example of the
reset/browse/verify loop and gives the demo a deterministic red/green story.

Usage: python -m env.simulate_agent [--task t2] [--seeds 0-9] [--strategy naive|smart|both]
"""
import argparse
import json

from .app import create_app
from .verifiers import load_tasks


def _get_json(client, url):
    r = client.get(url)
    assert r.status_code == 200, f"{url} -> {r.status_code}"
    return r.get_json()


def run_episode(client, spec, task, seed, strategy):
    r = client.post("/reset", query_string={"seed": seed})
    sid, entry = r.get_json()["session_id"], r.get_json()["entry_url"]
    steps = []

    def go(url):
        r = client.get(url)
        steps.append(url)
        return r

    go(entry)  # agent lands on the env

    api = spec["backend_calls"][0]["path"]
    first = _get_json(client, f"/s/{sid}{api}?page=1")
    steps.append(f"{api}?page=1")
    items = list(first["items"])

    if strategy == "smart":
        for p in range(2, first["total_pages"] + 1):
            items += _get_json(client, f"/s/{sid}{api}?page={p}")["items"]
            steps.append(f"{api}?page={p}")

    # Agent applies the task constraints to what it has SEEN (naive agents
    # only see page 1 — that's the failure being reproduced).
    params = task.get("params", {})
    pool = items
    if params.get("direct_only"):
        pool = [f for f in pool if f["direct"] == 1]
    if params.get("departs_after"):
        pool = [f for f in pool if f["departs_at"] >= params["departs_after"]]
    if not pool:
        return {"passed": False, "reason": "agent saw no matching items", "steps": len(steps)}
    sort_by = params.get("sort_by", "price")
    pick = sorted(pool, key=lambda f: (f[sort_by], f["price"], f["id"]),
                  reverse=params.get("order") == "desc")[0]

    detail = spec["routes"]["detail"]["path"].replace("<int:eid>", str(pick["id"]))
    go(f"/s/{sid}{detail}")
    mut = spec["routes"]["mutation"]["path"].replace("<int:eid>", str(pick["id"]))
    form = {f["name"]: "sim@agent.dev" for f in
            spec["mutations"][spec["routes"]["mutation"]["table"]]["form_fields"]}
    client.post(f"/s/{sid}{mut}", data=form)
    steps.append(mut)

    res = client.post("/verify", json={"task_id": task["id"],
                                       "session_id": sid}).get_json()
    res["steps"] = len(steps)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", default="t2")
    ap.add_argument("--seeds", default="0-9")
    ap.add_argument("--strategy", default="both", choices=["naive", "smart", "both"])
    ap.add_argument("--spec", default=None)
    args = ap.parse_args()

    lo, _, hi = args.seeds.partition("-")
    seeds = [int(lo)] if not hi else range(int(lo), int(hi) + 1)

    app = create_app(args.spec)
    spec = app.config["SPEC"]
    task = load_tasks()[args.task]
    client = app.test_client()

    strategies = ["naive", "smart"] if args.strategy == "both" else [args.strategy]
    for strat in strategies:
        results = [run_episode(client, spec, task, s, strat) for s in seeds]
        passed = sum(1 for r in results if r["passed"])
        print(f"{strat:>5}: {passed}/{len(results)} passed  (task {task['id']})")
        for s, r in zip(seeds, results):
            mark = "PASS" if r["passed"] else "FAIL"
            print(f"      seed {s:>2}  {mark}  {r['reason']}  [{r['steps']} steps]")


if __name__ == "__main__":
    main()
