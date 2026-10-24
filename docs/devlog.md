# Devlog — Failure-to-Env

Running log of implementation decisions, deviations from PLAN.md, issues hit,
and open questions. Newest entries at the bottom.

## 2026-10-24 — B: env skeleton + dev fixture (H1 deliverable)

### Built

- `env/` Flask package implementing the full B→C contract:
  - `POST /reset?seed=N[&popup=0|1&labels=i]` → `{session_id, entry_url, seed}`
  - `GET /s/<sid>/...` session-scoped env pages + stub APIs
  - `GET /state?session=<sid>` → full table dump + `meta` (seed, labels, popup)
  - `POST /verify {task_id, session_id}` → `{passed, reason}`
  - `GET /sessions` → session list (for the dashboard later)
- Session model: per-session SQLite file in `env/.sessions/<sid>.db`.
- Seeded generation (`env/seeds.py`): generator registry keyed by
  `spec.entities.*.generator`; flights fixture emits structured near-misses so
  the verifier's constraints actually bite (global price min is non-direct;
  direct/late/earliest minima are distinct flights).
- Perturbation knobs, all seed-driven: shuffled display order, item-count range
  (→ variable page count), label dictionary (4 sets), popup modal. `popup` and
  `labels` can be pinned via `/reset` query params (useful for C's held-out sets
  and for deterministic demos).
- Component slot: `env/component/` is on the Jinja search path; `index.html`
  does `{% include "component.html" %}`. Currently holds the **hand-written
  dev-fixture** flight-search component — this is the same seam the generator
  (or verbatim paste) will write into.
- Stub API: `env/stubs.py` turns `spec.backend_calls` into
  `/s/<sid>/<path>` routes serving session-DB rows, paginated
  `{items, page, per_page, total, total_pages}`.
- Verifiers (`env/verifiers.py`): `booked_matching_flight` — parametric argmin
  (`direct_only`, `departs_after`, `sort_by`, `order`) computed against live
  session data. `env/tasks.json`: 4 hand-written variants (t1–t4).
- Anti-cheat: every `/s/<sid>/` request is logged to a per-session
  `request_log` table; `booked_matching_flight` requires the booked flight's
  detail page to have been visited before the booking POST.
- `fixtures/` contract stubs for all four artifacts (trajectory, env_spec,
  tasks, rollouts, metrics) — the H1 deliverable.
- `env/tests/test_env.py`: 9 tests, all passing — reset/state, seed
  determinism, happy path, wrong-flight fail, anti-cheat reject, session
  isolation, variant divergence, popup toggle.

### How to run

```bash
pip install -r env/requirements.txt
python -m env.app          # serves on :5001 (PORT env to override)
python -m pytest env/tests # tests
```

### Decisions made during implementation (not in PLAN.md)

- **`<base href="/s/<sid>/">`** in `base.html` — all internal links and
  component fetches are relative and resolve under the session prefix. Keeps
  generated components free of session plumbing; one line does all the work.
- **Request log inside the session DB** rather than a separate store — it rides
  along in `/state` dumps, so verifiers and the dashboard see the same data.
- **Trap-first seed generation** — distractors are emitted explicitly (cheap
  non-direct, cheap direct, cheap post-17:00) and fill rows are clamped above
  them, so every seed exercises every task variant meaningfully.

### Deviations / flags for the team

- **Fixture routes are hardcoded** (`/flights/<id>`, `/book`, `/bookings/<id>`).
  Fine for the dev fixture; when `generate.py` lands, route wiring needs to be
  driven by `spec.pages` + `expected_mutation`. Noted seam, not yet generic.
- **`env_spec.json` grew two fields** beyond PLAN.md: `entities` (generator +
  count_range + columns) and `mutations` (table defs). `data_schema`/`seed_data`
  from the plan are subsumed; A should keep emitting them but B consumes
  `entities`/`mutations`. Fixtures stub updated to match.
- **`/verify` is env-side** as planned — differs from vision.md's literal
  `verify(task_id, state)` import contract. C should confirm at kickoff.
- **Popup is non-blocking by design** — it overlays with z-index and must be
  dismissed via the "No thanks" link to click underlying content in a real
  browser. Headless tests bypass it; worth watching whether the agent harness
  handles it.
- **`flight_no` assignment is post-shuffle sequential** (`FL100+i`), so flight
  numbers correlate with display position, not with price. Not a leak for
  current verifiers (nothing sorts by flight_no) but noted.

### Open / next

- `generate.py` (spec → component codegen + validation + verbatim fallback) —
  next build item.
- A's real `env_spec.json` — need a captured component early to iterate on
  codegen prompts.
- Verifier for popup-dismissal style tasks doesn't exist yet (tasks are all
  booking-argmin); add when A locks the failure mode.
- No rate limiting / session expiry — fine for localhost hackathon use.

## 2026-10-24 — B: spec-driven routing + generator (generate.py)

### Built

- **Routes are now spec-driven.** The earlier flag is resolved: `spec.routes`
  declares `entry` / `detail` / `mutation` / `confirmation` with path
  templates (`<int:eid>` = entity id, `<int:bid>` = mutation row id), and
  `app.py` registers views generically. Detail page renders any entity row +
  mutation form (`mutations.<table>.form_fields`); confirmation renders the
  mutation row + optional join display. Nothing in `app.py` knows "flights"
  anymore.
- **`env/generate.py`** — the codegen pipeline:
  - `build_prompt(spec)` — slot contract (partial-only HTML, relative fetches
    only, declared stub endpoints, allowed Jinja vars `labels`/`meta`/`sid`)
    + interaction description + entity schema + captured component as
    fidelity reference.
  - LLM via `default_llm()` — Anthropic or OpenAI picked from env keys,
    model via `ENV_LLM_MODEL`; injectable callable for tests (no key needed).
  - `static_validate` — no http(s) anywhere, no html/head/body in the
    partial, every `fetch()` must resolve under a declared stub/route path,
    `node --check` on the JS when node is installed.
  - `smoke_test` — boots the real app against a temp component dir: /reset,
    entry page serves and contains the component, component.js served, every
    backend_call returns 200.
  - Retry loop (3 attempts) feeds validation/smoke errors back into the
    prompt.
  - **Verbatim fallback** — writes captured html wrapped in `{% raw %}`
    (protects it from the Jinja include) and prepends `FETCH_SHIM` to the
    captured js, which monkeypatches `fetch` and `XHR.open` to reroute the
    original backend URLs (incl. absolute site_url forms) to env stubs.
  - Report written to `<component_dir>/generate_report.json`
    (`{path: generated|verbatim|failed, attempts, errors}`).
- CLI: `python -m env.generate <spec> [--component-dir DIR] [--verbatim]`.
- `create_app(spec_path, component_dir)` — component dir is parameterizable
  (env var `ENV_COMPONENT_DIR`) so smoke tests don't clobber the live slot.
- `index.html` renders a placeholder when no component exists yet.
- `fixtures/env_spec.json` + PLAN.md contract updated to the canonical shape
  (entities / mutations.form_fields / routes).

### Decisions made during implementation

- **Verbatim fallback wraps captured HTML in `{% raw %}`** — captured markup
  may contain `{{ }}` from its source framework; raw-wrapping keeps the
  Jinja include mechanism uniform while verbatim content stays literal.
- **Verifiers take `spec`** (`fn(conn, params, sid, spec)`) — the
  detail-visit anti-cheat check derives the required path from
  `spec.routes.detail.path` instead of hardcoding `flights/<id>`.
- **Component filename contract is fixed** (`component.html`/`component.js`)
  — the generator's only output surface; keeps the slot contract trivially
  checkable.
- **LLM calls use urllib** — zero new deps; provider pluggable via injected
  callable.

### Flags

- **`booked_matching_flight` verifier is still flights-shaped** (queries
  `flights`/`bookings` by name). That's fine — verifiers are hand-written
  per env — but a new domain needs a new verifier fn. Verifier *interface*
  is generic; verifier *bodies* are per-domain by design.
- **Codegen LLM path is untested against a real API** — needs a key and a
  real captured component from A. Validation/fallback mechanics are tested.
- **Fetched URL allowlist is prefix-based** — `fetch('api/flightsXYZ')`
  would pass the static check. Low risk; smoke test still gates.
- **Popup overlay may not actually block clicks** — it's a visual overlay;
  whether it intercepts Playwright clicks depends on the hit target.
  Deliberately; flag if the repro agent sails through it.
- The fixture spec's `component.html/js` are paths into `env/component/` —
  i.e., the fixture's "captured" component is the same file as the slot's
  dev content. Real specs will carry captured source (literal or path).

### Open / next

- Needs A's real captured component to exercise codegen for real.
- Optional: `POST /sessions/<sid>/delete` cleanup endpoint if C wants it.
- Second domain (stretch) = new spec + generator fn + templates reuse.

## 2026-10-24 — B: rollout simulator (integration reference for C)

### Built

- **`env/simulate_agent.py`** — scripted agent driving the real contract via
  the Flask test client (no server, no LLM). Two strategies:
  - `naive` — reads only page 1 of the paginated stub and books the cheapest
    *visible* match → reproduces the failure mode
  - `smart` — paginates through all pages → the fixed behavior
- CLI: `python -m env.simulate_agent --task t2 --seeds 0-9`.

**Result on t2 (cheapest direct), seeds 0–9: naive 4/10, smart 10/10.** On t1
(cheapest overall): naive 1/5, smart 5/5. Naive fails precisely when the
target flight sits past page 1 — the mini version of fail→reproduce→fix→green.

This doubles as C's integration reference (the exact request sequence a real
agent rollout should make: `reset → entry → api pages → detail → book →
verify`) and as a deterministic demo fallback if the live agent misbehaves.

### Flags

- **`/state` is unguarded** — an agent could read the full DB dump and skip
  pagination entirely (request_log only covers `/s/` paths, and the verifier
  doesn't forbid it). For the demo: instruct agents not to call `/state`, or
  gate it behind a header/token if needed. Flagged, not fixed — deciding
  whether "reading /state" counts as cheating is a semantics call worth a
  team conversation.
- Simulator assumes backend_calls[0] is the listing API — fine for the
  fixture; generalize if a spec has multiple calls.
