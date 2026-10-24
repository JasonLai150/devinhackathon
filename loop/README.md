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
| `browser` / `browser:llm` | A's shared Playwright + LLM strategy, with skill notes and the same model provider as capture |
| `browser:auto` | A's shared scripted browser strategy; switches to pagination on the memory cue, explicitly a rehearsal |
| `browser:naive` / `browser:smart` | Shared scripted browser reference strategies |

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

## Integrated toolshop run

See [`../docs/integration.md`](../docs/integration.md) for the validated commands.
Use `--spec fixtures/env_spec.json --task shop-cheapest` for A's domain.
The toolshop spec embeds its task set so the env and runner agree.
`browser:*` agents require a real HTTP env (not `--in-process`) and Chromium.
`loop.demo --agent browser:auto --real-transfer` uses the same memory-driven
strategy in the sandbox and on the public site, and A's checker reads the
real cart before the browser context closes. `/state` is blocked from the
agent's browser; it remains available to runner tooling.

Each demo writes `run_manifest.json`; the dashboard's `?artifacts=<directory>`
selects that run's metrics, reproduction evidence and real/rehearsal verdict.

## Stretch

GRPO on a small model for one task — only after the above is stable.

## Genuine LLM experiment

`python -m loop.experiment --preflight` checks credentials and browser dependencies.
`python -m loop.experiment --out .integration/llm-demo --headed` runs the
evidence-gated protocol using one fixed agent configuration. See
[`../docs/demo-runbook.md`](../docs/demo-runbook.md) for settings and artifacts.

The experiment distills only verified training successes, never evaluation
records or scripted hints. The curve includes a measured held-out baseline and
the same evaluation seeds at each later round. No improvement is a valid result;
it blocks transfer rather than being replaced by a scripted success.

`loop.demo` remains the scripted rehearsal; use `--record-dir <directory>`
for videos and step screenshots. Its curve now uses the same evaluation
protocol, but it is not an LLM-learning result.
