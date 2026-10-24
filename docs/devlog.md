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
