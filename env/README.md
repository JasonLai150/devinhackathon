# env — the sandbox (workstream B)

Spec-driven mini-env: deterministic Flask skeleton + a component slot the
generator fills. See `../PLAN.md` for the contract and `../docs/devlog.md`
for the running log.

## Run

```bash
pip install -r requirements.txt
python -m env.app            # :5001 (PORT to override)
python -m pytest env/tests   # 15 tests
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
ANTHROPIC_API_KEY=... python -m env.generate spec/env_spec.json
python -m env.generate spec/env_spec.json --verbatim   # force paste fallback
```

Writes `component/component.html` + `component.js` + `generate_report.json`.
Provider picked from `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`; model via
`ENV_LLM_MODEL`.

## Spec anatomy

`spec/env_spec.json` drives everything: `entities` (tables + generator),
`mutations` (+ `form_fields`), `routes` (entry/detail/mutation/confirmation
path templates), `backend_calls` (→ stub API routes), `component` (captured
html/js or paths), `interaction` (codegen prompt input). Canonical example:
`../fixtures/env_spec.json`.

## Perturbation knobs (seed-driven)

Shuffled row order, item-count range → variable page count, 4 label sets,
popup modal. `popup`/`labels` can be pinned per-reset for deterministic eval.
