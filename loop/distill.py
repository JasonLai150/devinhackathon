"""Distill episode outcomes into skill notes (rejection sampling).

Memory CONTENT comes only from what verified episodes demonstrate —
successes are evidence, failures are verifier feedback (reason doubles
as reward detail). Nothing site-specific is memorized: the notes hold
general rules.

With an LLM key set, outcomes are distilled by the model; without one, a
deterministic heuristic derives rules from the traces so the loop still
runs end-to-end (and tests stay offline).
"""
import json
import re

DISTILL_PROMPT = """\
You maintain "skill notes" for a web-browsing agent. They are injected
into the agent's context before each episode — concrete, general rules
it can follow, not a narrative.

Current skill notes:
{notes}

Verified-successful episodes (task, action trace, verifier reason):
{successes}

Failed episodes (verifier feedback — what went wrong):
{failures}

Rewrite the notes: keep rules that still hold, generalize from the
successes, and add rules that would have prevented the failures. Rules
should be imperative and specific (e.g. "always paginate through every
results page before comparing prices"). Respond with ONLY the markdown
notes."""


def _format(records):
    return json.dumps([
        {"task_id": r["task_id"], "seed": r["seed"],
         "verifier_reason": r["reason"],
         "actions": r.get("actions", [])}
        for r in records], indent=2)


def _heuristic(notes, successes, failures):
    """Derive rules from what the traces actually show."""
    lines = [l for l in notes.splitlines() if l.strip()]
    seen = {l.strip().lower() for l in lines}

    def add(rule):
        key = rule.strip().lower()
        if key not in seen:
            seen.add(key)
            lines.append(rule)

    # Pagination evidence: a success that visited later pages proves the
    # rule; failures whose verifier says the expected item differed while
    # no trace fetched page >= 2 are the same signal from the other side.
    paginated = any(
        re.search(r"\?page=([2-9]|\d\d+)", a)
        for r in successes for a in r.get("actions", []))
    missed_later_pages = failures and not any(
        re.search(r"\?page=([2-9]|\d\d+)", a)
        for r in failures for a in r.get("actions", []))
    if paginated or missed_later_pages:
        add("- Always paginate through ALL result pages "
            "(?page=1..total_pages) before choosing — the best match is "
            "often not on page 1.")

    if any(a.startswith("POST ") for r in successes
           for a in r.get("actions", [])):
        add("- Visit the item's detail page before submitting the "
            "booking/mutation form.")

    if not lines:
        lines.append("# Skill notes")
    elif not lines[0].startswith("#"):
        lines.insert(0, "# Skill notes")
    return "\n".join(lines) + "\n"


def distill(notes, successes, llm=None, failures=None):
    """successes: passed rollout records. failures: failed records
    (verifier feedback). Returns updated notes text."""
    failures = failures or []
    if not successes and not failures:
        return notes
    if llm is not None:
        try:
            return llm(DISTILL_PROMPT.format(
                notes=notes or "(none yet)",
                successes=_format(successes),
                failures=_format(failures)))
        except Exception:
            pass  # fall back to the deterministic path
    return _heuristic(notes, successes, failures)
