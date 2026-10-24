"""Seed splits + parsing.

Held-out eval contract (kickoff decision): improvement rounds may only
ever see TRAIN_SEEDS; EVAL_SEEDS plus pinned perturbation overrides
(popup/labels) measure transfer on conditions training never saw.
"""
TRAIN_SEEDS = list(range(0, 20))
EVAL_SEEDS = list(range(100, 110))

# Pinned per-reset knobs so eval always includes the popup + a label set
# training may not have seen. Empty dict = fully seed-driven.
TRAIN_OVERRIDES = {}
EVAL_OVERRIDES = {"popup": "1", "labels": "3"}


def parse_seeds(text):
    """'0-9' | '0-9,20,25-30' | '5' -> [ints]."""
    out = []
    for part in str(text).split(","):
        lo, _, hi = part.partition("-")
        out.extend([int(lo)] if not hi else range(int(lo), int(hi) + 1))
    return out
