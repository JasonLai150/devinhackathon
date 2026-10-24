# loop — rollouts + improvement (workstream C)

Runs the agent at scale against B's env, reproduces the baseline failure,
distills verified outcomes into skill-note memory, and measures transfer.
Delivers `rollouts.jsonl`, `metrics.json`, `agent_config.json` (the
improved-agent config). See `../PLAN.md` and `../docs/devlog.md`.

## Quick start

```bash
pip install -r loop/requirements.txt   # + env/requirements.txt
python -m env.app                      # B's env on :5001

python -m loop.demo                    # full pipeline vs the live env
python -m loop.demo --in-process       # same, no server needed (offline)
python -m pytest loop/tests env/tests  # all tests
```

`loop.demo --in-process` is the deterministic fallback: no LLM key, no
browser, no server — it regenerates every committed artifact in
`loop/artifacts/` and shows the full red -> green story.

## Pipeline commands

```bash
# rollouts: reset -> agent -> verify -> rollouts.jsonl
python -m loop.run_rollouts --agent sim:naive --task t2 --seeds 0-9

# round 0: does the baseline fail the same way as on the real site?
python -m loop.repro_check --agent sim:naive --task t2 --seeds 0-9

# improvement: train on TRAIN_SEEDS -> distill skill_notes.md -> eval on
# held-out EVAL_SEEDS (+ pinned popup/labels) -> metrics.json
python -m loop.improve --agent sim:auto --task t2 --rounds 3

# transfer: improved agent + notes on the real site (A's checker)
python -m loop.transfer --config loop/artifacts/agent_config.json
python -m loop.transfer --rehearse --agent sim:smart --in-process
```

All commands take `--env-url` (default `$LOOP_ENV_URL` or
`http://localhost:5001`) or `--in-process` to drive `env.app` directly.

## Agents

| name        | what it is |
|-------------|------------|
| `sim:naive` | scripted, page-1-only — reproduces the pagination failure |
| `sim:smart` | scripted, paginates fully — the fixed behavior |
| `sim:auto`  | naive until `skill_notes` contain the pagination cue — models memory injection deterministically |
| `browser`   | A's real harness (browser-use / Playwright+LLM, locked at kickoff) — `agent_browser.py` is the adapter seam |

## Contracts

- **Consumes (B):** `POST /reset?seed=[&popup&labels]`, `GET /s/<sid>/`,
  `GET /state`, `POST /verify`, `env/tasks.json`, `env/spec/env_spec.json`
- **Consumes (A):** `fixtures/trajectory.json` (repro comparison),
  `site_url` (transfer target), real-site checker via
  `LOOP_REAL_CHECKER=module:callable`
- **Produces (dashboard):** `loop/artifacts/rollouts.jsonl`
  `{round, task_id, seed, passed, reason, steps, ...}` per episode;
  `loop/artifacts/metrics.json` `{metrics: [{round, success_rate, n}]}`
- **Produces:** `agent_config.json`, `skill_notes.md`, `repro_report.json`,
  `transfer_result.json`

## Held-out eval rule

Improvement rounds may only see `seeds.TRAIN_SEEDS`. Metrics come from
`EVAL_SEEDS` + `EVAL_OVERRIDES` (popup pinned on, a label set training may
not have seen). Agent episodes never touch `/state` — it's unguarded and
would trivialize the tasks; runner tooling uses it only after the episode.

## What flips when A lands

- `agent_browser.py` — wrap A's run-agent callable; `skill_notes` go in
  the system prompt. Everything downstream is unchanged.
- `LOOP_REAL_CHECKER=capture.checker:check` — real transfer verdicts.
- `fixtures/trajectory.json` — repro gains the real `failure_step`
  reference (currently compares against `env_spec.failure_note`).

## Stretch

GRPO on a small model for one task — only after the above is stable.
