"""Real-site success checker — A's deliverable, stubbed until it lands.

Set LOOP_REAL_CHECKER=module.path:callable once A ships their checker.
The callable signature:

    check(task: dict, target_url: str, outcome: dict)
        -> {"passed": bool, "reason": str}

outcome carries the agent's trace: {actions, steps, error}. Until the
env var is set (or the module fails to import), the stub reports a clear
not-integrated verdict instead of a fake pass — the demo must never show
a green that isn't real.
"""
import importlib
import os


def check_transfer(task, target_url, outcome):
    ref = os.environ.get("LOOP_REAL_CHECKER", "").strip()
    if ref:
        mod_name, _, fn_name = ref.partition(":")
        try:
            fn = getattr(importlib.import_module(mod_name),
                         fn_name or "check")
        except Exception as e:
            return {"passed": False,
                    "reason": f"LOOP_REAL_CHECKER={ref!r} failed to load: {e}"}
        return fn(task, target_url, outcome)
    if outcome.get("verification") is not None:
        return outcome["verification"]
    return {"passed": False,
            "reason": "real-site checker not integrated "
                      "(set LOOP_REAL_CHECKER=module:callable)"}
