# env — the sandbox (workstream B)

Spec-driven mini-env: deterministic Flask skeleton + a component slot the
generator fills. See `../PLAN.md` for the contract and `../docs/devlog.md`
for the running log.

## Run

```bash
pip install -r env/requirements.txt
python -m env.app            # :5001 (PORT to override)
python -m pytest env/tests
```

## Contract (for C)

```
POST /reset?seed=N[&popup=0|1&labels=i]  → {session_id, entry_url, seed}
GET  /s/<sid>/...                        → the env; agent browses here
GET  /state?session=<sid>                → full DB dump + meta
POST /verify  {task_id, session_id}      → {passed, reason}
GET  /sessions                           → session list
```

Each session is its own SQLite file (`env/.sessions/<sid>.db`) — run rollouts
in parallel freely. Expected verifier answers are computed from live seeded
state, so any seed works.

## Generate an env component

```bash
python -m env.generate env/spec/env_spec.json
python -m env.generate fixtures/env_spec.json --component-dir .integration/component --verbatim
```

Writes `component/component.html` + `component.js` + `generate_report.json`.
Provider picked from `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`; model via
`ENV_LLM_MODEL`.

Commands assume the repository root. See `../docs/integration.md` for the
connected toolshop run. Its spec embeds four tasks; `/tasks` returns the
active task set and `/verify` uses that same set. The default `env/spec` and
`env/tasks.json` remain the flights development fixture.

## Simulate a rollout (integration reference for C)

```bash
python -m env.simulate_agent --task t2 --seeds 0-9
```

`naive` (page-1-only) reproduces the pagination failure — ~4/10 pass on t2.
`smart` (paginates fully) goes 10/10. Same request sequence a real rollout
makes: reset → entry → api pages → detail → book → verify.

## Spec anatomy

`spec/env_spec.json` drives everything: `entities` (tables + generator),
`mutations` (+ `form_fields`), `routes` (entry/detail/mutation/confirmation
path templates), `backend_calls` (→ stub API routes), `component` (captured
html/js or paths), `interaction` (codegen prompt input). Canonical example:
`../fixtures/env_spec.json`.

## Perturbation knobs (seed-driven)

Shuffled row order, item-count range → variable page count, 4 label sets,
popup modal. `popup`/`labels` can be pinned per-reset for deterministic eval.

For the toolshop, `python -m env.generate fixtures/env_spec.json
--component-dir .integration/component --browser-check` additionally exercises
pagination and a verified cart mutation in Chromium. Add `--no-fallback` to
require LLM-generated code; `--verbatim` explicitly selects the supplied
reference. The report includes actual generation/validation elapsed time.
