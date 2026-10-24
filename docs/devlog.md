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

## 2026-10-24 — C: rollouts + improvement loop (loop/)

### Built

- `loop/` package implementing C's deliverables:
  - `env_client.py` — env contract client, two transports (real HTTP for
    rollouts, in-process Flask test client for tests).
  - `agent.py` / `sim_agent.py` / `agent_browser.py` — agent interface,
    B's naive/smart strategies ported to the client, and the adapter seam
    for A's real harness (`sim:auto` flips naive→smart on a skill-notes
    cue, modeling memory injection deterministically).
  - `runner.py` / `run_rollouts.py` — parallel episodes
    (`reset → agent.run → verify → rollouts.jsonl`), baseline cache keyed
    by (task, seed, agent, memory hash).
  - `repro_check.py` — round 0; classifies each failure against the
    pagination signature from session `request_log` + tables.
  - `improve.py` + `memory.py` + `distill.py` + `llm.py` — rejection
    sampling into `skill_notes.md`; LLM distills when a key is set,
    deterministic heuristic otherwise.
  - `transfer.py` + `real_site_checker.py` — improved agent on the real
    site via `LOOP_REAL_CHECKER=module:callable`; `--rehearse` runs the
    whole path against a fresh env session + env `/verify`.
  - `demo.py` — repro → improve → transfer-rehearse in one command;
    `--in-process` regenerates the committed artifacts with no LLM,
    browser, or server (the pre-recorded fallback).
- `loop/artifacts/` — committed deliverables: `rollouts.jsonl`,
  `metrics.json` (0.4 → 1.0 → transfer 1.0), `repro_report.json`,
  `skill_notes.md`, `agent_config.json`, `transfer_result.json`.
- `loop/tests/`: 34 tests, all passing. Verified end-to-end:
  `python -m loop.demo --in-process` → repro REPRODUCED (6/6 failures
  on-signature) → eval 100% on held-out seeds → transfer PASS.

### Decisions made during implementation (worth a team conversation)

- **Distillation consumes verifier failure reasons, not just successes.**
  Rejection sampling keeps only verified episodes, but pure success-
  cloning can't learn the fix: naive's lucky page-1 passes contain no
  pagination evidence, so notes distilled from successes alone never gain
  the rule. `distill(notes, successes, failures=...)` passes failure
  records in as *feedback* (the plan already designates `reason` as
  "reward detail"); the notes themselves still hold only general rules.
- **Agents never touch `/state`.** It's unguarded and would trivialize
  every task (full DB dump). Agents get only `entry_url`; the runner uses
  `/state` strictly post-episode — for the repro classifier reading
  `request_log`, never mid-run. If the team decides reading `/state`
  should count as cheating, B could gate it behind a header/token — the
  loop side already behaves as if it's gated.
- **Cached records re-tagged on reuse.** A cache hit returns the stored
  episode with the *requesting* round/phase — same (task, seed, agent,
  memory) key means a deterministic-equivalent episode.
- **`sim:auto` is the memory-injection model for tests/fallback.** It
  flips to `smart` when skill notes contain "paginate" — a deterministic
  stand-in for a real agent reading notes in its system prompt. Not a
  claim that a real agent will follow notes this reliably.
- **`--rehearse` is the honest green.** Transfer rehearses on a fresh env
  session judged by env `/verify` — clearly labeled `mode: "rehearse"` in
  `transfer_result.json`. The stub checker reports "not integrated"
  rather than faking a pass; no green shown that isn't real.

### Flags

- **Flips when A lands:** `agent_browser.py` wraps A's run-agent callable
  (skill_notes → system prompt); `LOOP_REAL_CHECKER=capture.checker:check`
  for real transfer verdicts; `fixtures/trajectory.json` adds the real
  `failure_step` reference to `repro_report.json` (currently anchored on
  `env_spec.failure_note`).
- **`/s/<sid>`-style session cleanup:** repeated runs leave
  `env/.sessions/*.db` files behind; B noted an optional
  `POST /sessions/<sid>/delete` endpoint — C would use it in the runner.
- **Heuristic distillation is env-shaped.** The offline fallback knows
  pagination + detail-before-POST; a second domain needs new heuristic
  rules or an LLM key. The LLM path is domain-agnostic.
- **Contract additions on rollouts records:** `session_id`, `agent`,
  `phase`, `actions` ride alongside the agreed keys — needed for
  distillation and the dashboard's trace view; flag if A's reader
  requires a strict shape.
## 2026-10-24 — A: capture harness + real failure + dashboard

### Locked decisions (A's kickoff items)

- **Site:** `https://practicesoftwaretesting.com` — purpose-built automation
  practice app ("Toolshop"), no login, no CAPTCHA, real REST backend
  (`api.practicesoftwaretesting.com`).
- **Task:** "Add the cheapest product in the store to your cart" — mutation =
  cart add, checkable on `/checkout`.
- **Failure mode:** **pagination.** Live argmin price ("Washers" $3.55) sits on
  page 4–5; a page-1-only agent picks "Slip Joint Pliers" $9.17. Reproduced
  reliably — confirmed twice.
- **Agent:** Playwright harness (`capture/run_agent.py`) with two scripted
  strategies (`naive` = the failing baseline, `smart` = paginate-then-compare)
  + an `llm` strategy for the real agent loop. Scripted mode needs no API key —
  it's also the deterministic demo fallback. `--memory file` injects skill
  notes (C's transfer run reuses this harness).

### Built

- `capture/run_agent.py` — per-step screenshots + DOM + url into
  `fixtures/shots/<run>/`, `fixtures/dom/<run>/`; writes contract
  `trajectory.json` (+ `checker`, `expected`, `duration_s` extras).
- `capture/checker.py` — real-site checker `{passed, reason}`: live
  argmin(price) over the site API, cart contents parsed from `/checkout`
  (rows keyed by `data-test="product-title"` — no product ids in the DOM).
  `llm_judge` fallback for unscripted tasks.
- `capture/capture_component.py` — saves the real listing markup
  (`fixtures/captured/real_markup.html`, ~24KB verbatim) + a hand-recreated
  `component.html`/`component.js` implementing the same paginated-fetch
  interaction against `api/products` (relative fetch → resolves under the
  session `<base href>` to B's stub). **Honesty note:** markup is verbatim;
  JS is hand-recreated — the site's real JS is a minified Angular bundle.
- `capture/localize.py` — `env_spec.json` builder: deterministic (default) or
  `--llm` draft. Spec written to `fixtures/env_spec.json` (canonical contract).
- `env/seeds.py` — added `products` generator (trap-first: global argmin is
  pinned to the back half of listing order so page-1-only agents always miss;
  cheapest Pliers is a distinct row for category variants).
- `dashboard/` — static seven-stage timeline (`index.html`/`app.js`/`style.css`)
  reading `fixtures/*` + `env/tasks.json` + `env/component/generate_report.json`;
  canvas success-rate chart; live `/sessions` table when the env is up.
  `serve.py` serves repo root → `http://localhost:8000/dashboard/`.
- `capture/shoot_env.py` — boots env on the real spec + captured component,
  screenshots entry → `fixtures/shots/env/index.png` (side-by-side panel).
- `capture/record_fallback.sh` — regenerates every artifact the dashboard
  reads, end to end, no LLM key needed. Playwright videos land in
  `fixtures/video/<run>/` as demo footage.

### Verified end-to-end

- Naive run on real site: **FAILED** as designed — `fixtures/trajectory.json`
  (5 steps, failure_step=3, picked $9.17 page-1 item vs true $3.55 argmin).
- Env boots on the real spec (`ENV_SPEC=fixtures/env_spec.json
  ENV_COMPONENT_DIR=fixtures/captured`): reset → `api/products` paginated stub
  (39 products/5 pages, argmin "Washers" $3.85 at position 22) → component
  renders grid+pagination → detail POST writes `cart_items` row → request_log
  records the whole path.
- Smart run on real site: **PASSED** — `fixtures/trajectory_transfer.json`
  (paginates all 5 pages, adds Washers). Doubles as the transfer-run evidence.

### Changes in B's files (integration, flagged)

- `env/seeds.py`: added `products` generator (required by A's spec).
- `env/sessions.py`: `create_session` removes the DB file if seeding raises
  (half-seeded files were breaking `/sessions`); `list_sessions` skips
  malformed DBs.
- `env/app.py`: `Access-Control-Allow-Origin: *` on `/sessions` so the
  dashboard (different localhost port) can fetch it.
- **Not done (B owns):** `env/spec/env_spec.json` still holds the flights dev
  fixture — the real spec lives at `fixtures/env_spec.json`; boot with
  `ENV_SPEC` env var (see `capture/shoot_env.py`). Verifiers/tasks.json are
  still flights-shaped; need a `cart_contains_product`-style verifier for the
  products domain (argmin over `products`, check `cart_items.product_id`).
- B's tests: all 17 pass — they exercise the flights fixture, unaffected.

### Deviations / flags for the team

- **`env_spec.json` grew `component.reference_markup`** — path to the verbatim
  captured DOM, kept as codegen fidelity reference separate from the
  hand-recreated `component.js`.
- **Checkout DOM has no product ids** — checker matches on normalized
  name + price, not id. Collision risk is negligible here but noted.
- **practice API is flaky** (seen timeouts/500s under rapid paging) —
  `checker._get_json` retries w/ backoff; dashboard + `record_fallback.sh`
  are the live-demo safety net.
- **Mutation shape differs from flights:** "add to cart" isn't a one-shot
  booking; env maps it to `POST /products/<eid>/cart` → `cart_items` row,
  confirmation at `/cart-items/<bid>` (not a cart-overview page — B may want
  a `/cart` route shape later if the demo wants a visible cart page).
- Video capture uses `--video` (playwright `record_video_dir`) — webm files
  under `fixtures/video/` are committed for the fallback reel.

## 2026-10-24 — Integration: A + B + C over HTTP and Chromium

Pulled through `b741fe1`. The existing 51 tests passed, but the shop spec
failed `/verify` with a flights-table error; fallback generation incorrectly
treated captured file paths as source; C's browser adapter was a stub; and the
dashboard consumed placeholder metrics. These integration seams are fixed.

Full findings, changed contracts, repeatable commands and limitations:
[`integration.md`](integration.md). Compact measured evidence:
[`integration-results.json`](integration-results.json).

Changes include spec-contained shop tasks, cart verification/quantity validation,
source-path resolution, shared browser strategies with entry URLs, live-context
transfer checks, query-preserving logs, stricter repro classification, cache
input/lifetime checks, and a dashboard run manifest. Capture localization now
leaves the flights fixture intact. The generator still defaults to LLM-first;
the integration run explicitly selected the disclosed reference fallback.

Measured: fresh real-site failure, 3/3 matching sandbox failures, 3/3 held-out
browser successes with memory, then real-site cart success (Washers $3.55,
checked against 50 products). **61 tests passed**, including a local HTTP +
Chromium regression. Dashboard loaded all seven stages without JavaScript
errors. Raw artifacts are retained locally under `.integration/` (gitignored).

Flag: `browser:auto` + heuristic notes is a scripted rehearsal, not LLM
learning. There were no LLM keys in the shell, so credentialed generation and
autonomous learning remain unverified. The real site's UI exposed 45 products
while the checker's API returned 50; the selected minimum matched both.

Publication race: teammate commit `ba7a4a7` redesigned the dashboard while this
integration was running. Rebased onto it and preserved the KPI/chart/matrix UI;
the manifest-based data sources, transfer labels and screenshot binding remain.
Rechecked the redesigned dashboard in Chromium, including episode selection.
