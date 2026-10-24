# Implementation Plan — Failure-to-Env

Turning real web-agent failures into resettable, verifiable training environments.
Companion to `vision.md`. This document is the build plan for all three workstreams.

## Locked decisions

| Decision | Choice |
|---|---|
| Stack (env + tooling) | Python: Flask + SQLite + Jinja for the env; Playwright + LLM for the agent |
| Component in env | LLM-generated into a bounded slot; verbatim HTML/JS paste as wired-in fallback |
| Session model | `POST /reset?seed=N` → `session_id`; all env routes under `/s/<sid>/`; per-session SQLite file |
| Task model | Mutations only — agent actions must change DB state; verifiers check DB for the correct record |
| Verification | `POST /verify {task_id, session_id}` → `{passed, reason}`, implemented inside the env |
| Task variants | Hand-written `tasks.json` per env; expected answers derived from live seeded DB, never hardcoded |
| Env domain | Generic skeleton first; domain plugs in via spec after A locks the failure mode at H1 |
| Perturbations | Reorder + distractors, extra pages, renamed labels, popup/modal injection — all seed-driven |
| Deployment | Localhost everywhere (dev, rollouts, demo); pre-recorded run as fallback |

## Pipeline

```
real site ──A──> trajectory.json ──A──> env_spec.json ──B──> mini-env + tasks.json + verifiers
                                                              │
C: POST /reset?seed → agent runs on /s/<sid>/ ──> POST /verify ─┘
   rollouts.jsonl + metrics.json ──> dashboard (A)
   improved agent ──> transfer run on real site (reuses A's harness)
```

## Contracts

Commit stubs of all four at H1; everyone builds against stubs until real data lands.

### `trajectory.json` (A → C, dashboard)

```json
{
  "task": "find the cheapest item across all pages",
  "site_url": "https://...",
  "steps": [{"i": 0, "action": "...", "url": "...", "screenshot_path": "...", "dom_path": "..."}],
  "outcome": "failed",
  "failure_step": 7
}
```

### `env_spec.json` (A → B)

Extends the vision draft with fields B's generator needs:

```json
{
  "site_url": "...",
  "pages": [{"name": "...", "role": "list|detail|form"}],
  "component": {"name": "...", "html": "...", "js": "..."},
  "backend_calls": [{"method": "GET", "path": "/api/flights", "response_shape": {}}],
  "interaction": "date picker only works via calendar grid; text input ignored",
  "data_schema": {"flights": {"id": "...", "price": "...", "...": "..."}},
  "seed_data": {},
  "expected_mutation": "a bookings row with flight_id matching argmin(price)",
  "failure_step": 7,
  "failure_note": "agent paginated only once, missed page 3"
}
```

- `backend_calls`: endpoints the captured component hits, so B's shim can reroute them to env stubs.
- `interaction`: natural-language description of the breaking interaction — feeds the codegen prompt.
- `expected_mutation`: what a correct run writes to DB — gives verifier generation a target.
- `failure_step`: anchors C's repro check.

### Env API + `tasks.json` (B → C)

```
POST /reset?seed=N          → {session_id, entry_url}
GET  /s/<sid>/...           → the env; agent browses here
GET  /state?session=<sid>   → full DB dump + seed + perturbations applied
POST /verify                → {task_id, session_id} → {passed, reason}
```

```json
tasks.json: [{"id": "t1", "instruction": "book the cheapest direct flight after 5pm Friday",
              "verifier": "booked_matching_flight", "params": {}, "seed": 42}]
```

### `rollouts.jsonl` + `metrics.json` (C → dashboard)

```jsonl
{"round": 0, "task_id": "t1", "seed": 42, "passed": false, "reason": "...", "steps": 12}
```

```json
{"metrics": [{"round": 0, "success_rate": 0.1, "n": 10}]}
```

## Repo layout

```
/capture     (A)  agent harness, trajectory logging, real-site checker, spec localization
/env         (B)  env template, generator, tasks, verifiers
/loop        (C)  rollout runner, improvement loop, metrics
/dashboard   (A)  demo UI: timeline of the seven stages
/fixtures    (all) contract stubs committed at H1
```

One Devin session per folder keeps PRs from colliding.

---

## Teammate A — Capture + Dashboard ("the real world")

Owns everything that touches the real site, plus the demo UI.

### Deliverables

`trajectory.json`, `env_spec.json`, dashboard.

### Components

1. **Agent harness** (`/capture/run_agent.py`)
   - Off-the-shelf agent (browser-use, or Playwright + LLM — pick once, use everywhere including C's rollouts and the transfer run).
   - Per step logs: action, URL, screenshot (`fixtures/shots/`), DOM snapshot. Final outcome + failure step index → `trajectory.json`.

2. **Real-site success checker**
   - Scripted check where possible (e.g., assert cart contents / booking record); LLM judge as fallback.
   - Same checker semantics as env verifiers: `{passed, reason}`.

3. **Hour-1 failure selection**
   - Test 3–5 candidate tasks on the chosen demo site; pick the most reliable failure.
   - Candidates: pagination, custom dropdowns, date pickers, sort/filter state, hover/modal actions.
   - Share the failing trajectory with B and C immediately.

4. **Failure localization**
   - Hand-write `env_spec.json` first (MVP), then automate: LLM reads trajectory + captured component HTML/JS → spec.
   - Must capture the component's backend calls for B's stub generation.

5. **Dashboard** (`/dashboard`)
   - Timeline of all seven stages: failure replay → spec → mini-env → red repro → improvement chart → green transfer → Devin session timeline.
   - Reads artifacts (`trajectory.json`, `rollouts.jsonl`, `metrics.json`); side-by-side screenshots of real site vs mini-env; live success-rate chart.
   - Keep it static-file friendly so cached results survive a live-demo failure.

6. **Fallback recording**
   - Full run pre-recorded; dashboard served from cached artifacts.

### Open decisions for A (proposed — confirm at kickoff)

- Agent choice: **browser-use** recommended (batteries-included trajectory logging, model-agnostic).
- Demo site: pick from public automation-practice sites (e.g., a sandbox shop/booking site), no login/CAPTCHA.
- Localization automation priority: hand-written spec is acceptable for MVP; automate only after env loop works.

---

## Teammate B — Env Generator + Verifiers ("the sandbox")

Owns turning a spec into a runnable, verifiable environment.

### Deliverables

Running mini-env URL (localhost), `tasks.json`, `verifiers.py`.

### File layout (`/env`)

```
/env
  app.py            # Flask: /reset, /state, /verify, /s/<sid>/* routes
  sessions.py       # session lifecycle: create, seed, per-session DB
  seeds.py          # seeded RNG → data generation + perturbation decisions
  stubs.py          # spec.backend_calls → stub routes serving seed data
  generate.py       # env_spec.json → component codegen (LLM) + validation
  component/        # GENERATED: component.html (Jinja partial), component.js
  templates/        # base layout; generic list / detail / form page shells
  tasks.json        # hand-written task variants per env
  verifiers.py      # verify(task_id, sid) -> {passed, reason}
  spec/             # env_spec.json (stub until A delivers)
  .sessions/        # per-session sqlite files (gitignored)
```

### Session model

- `POST /reset?seed=N` → creates `session_id`, creates `.sessions/<sid>.db`, seeds it, applies seeded perturbation decisions → returns `{session_id, entry_url}`.
- All app routes live under `/s/<sid>/...`; base template injects `{{ url_prefix }}` into every internal link. URL-scoping is agent-visible and immune to shared-browser collisions.
- `GET /state?session=<sid>` dumps the session DB (all tables + seed + applied perturbations).
- Per-session DB files = zero shared mutable state → C's N parallel rollouts can't collide. Cleanup = delete file.

### Generic skeleton

Domain-agnostic concepts only — everything domain-specific lives in the component slot + seed data:

- **Entities**: `data_schema` → mechanically generated `CREATE TABLE` + seed inserts (no LLM).
- **Page shells**: list page (pagination controls), detail page, mutation form page. Spec's `pages` array wires which exist and where the component slot lands.
- **Component slot**: marked region where generated `component.html`/`component.js` is included — the failing interaction lives here.
- **Mutation endpoints**: generated from `expected_mutation` (e.g., booking POST writing a row).

### Generator (`generate.py`)

1. **Mechanical (no LLM):** schema → DDL+seeds; `backend_calls` → stub routes under `/s/<sid>/api/...` returning seed data; page wiring.
2. **LLM component codegen:** prompt = `interaction` description + captured component HTML/JS *as reference* + data schema + available stub endpoints + slot contract (self-contained partial; may only call stub endpoints; no external URLs). Output: `component.html`, `component.js`.
3. **Validation:** static checks (no external URLs, only stub endpoints referenced) + smoke test (boot env, `/reset`, load page, exercise stubs, headless render check). N retries with error feedback → **fallback**: paste `spec.component.html` verbatim + a fetch shim rewriting its backend calls to stub endpoints.
4. **Fidelity check:** cheap check that the generated component contains the interaction elements described in `interaction` (e.g., calendar grid exists if the failure is a date picker).

Demo framing: "the LLM regenerates the interaction component; the app shell is a fixed template." If codegen underperforms, verbatim fallback still makes the demo.

### Perturbations (seeded, applied at reset)

- **Reorder + distractors**: seed RNG shuffles item order; generate near-miss rows (e.g., a cheaper-looking flight that violates a constraint).
- **Extra pages**: variable item count / page size so "check all pages" can't be hardcoded.
- **Renamed labels**: per-seed label dictionary ("Search" → "Find flights").
- **Popup injection**: per-seed interstitial modal the agent must dismiss.

### Tasks + verifiers

- `tasks.json` hand-written per generated env: original task + 3–5 variants.
- `verify(task_id, sid)` computes the expected answer **from the live session DB** — e.g., "book cheapest direct flight after 5pm Friday" → argmin over `flights` where `direct AND departs_after=17:00`, then check `bookings` row matches. Perturbations can't break it.
- `reason` doubles as reward detail for C and anti-cheat evidence.

### Anti-cheat

Every request under `/s/<sid>/` logged to a per-session `request_log` table. Verifiers can require "results page visited before booking POSTed" — a cheat hitting the mutation endpoint directly without navigating is rejected with a reason. Covers the hand-made-cheating-trajectory deliverable.

### Build order

- **Now → H1:** contract stubs committed; skeleton app with working `/reset`, sessions, `/state`, stub `/verify`; generic page shells; seeded RNG utils. All domain-free.
- **H1 → H4:** hand-wire A's spec → seed loader + stub routes; **verbatim component paste first** (guarantees H4 "env serves the failing component" checkpoint); then `generate.py` LLM path + validation.
- **H4 → H6:** `tasks.json` variants, real `verifiers.py`, request-log anti-cheat, all four perturbations. End-to-end loop test with C.
- **H6 → H8:** harden parallelism, stretch second domain, `GET /sessions` hook for the dashboard.
- **H8:** freeze — fixes only.

### Risks

1. Codegen eats the day → verbatim-first sequencing; H4 never depends on LLM quality.
2. Generated component doesn't reproduce the failure → C's repro check is the real test; fidelity check in validation.
3. Parallel state bugs → per-session DB files + URL-scoped routing eliminate the class.
4. Verifier false-pass → expected answers derived from live state + request-log checks.

---

## Teammate C — Rollouts + Improvement ("the learning loop")

Owns running the agent at scale and making it better.

### Deliverables

`rollouts.jsonl`, `metrics.json`, improved agent config.

### Components

1. **Rollout runner** (`/loop/run_rollouts.py`)
   - N parallel episodes per task: `POST /reset?seed=` → run agent with `instruction` + `entry_url` → `POST /verify` → append to `rollouts.jsonl`.
   - Uses the **same agent + model** as A's harness (locked in vision).
   - Small N (5–10), short tasks, variants in parallel; cache the baseline round.

2. **Repro check**
   - Baseline agent on the mini-env should fail the same way as on the real site (compare against `failure_step`/`failure_note` in the spec). This is the credibility moment — log it as round 0.

3. **Improvement loop**
   - Keep verified successes; distill into a **skill note / few-shot memory** injected into the agent's context on later rounds (rejection sampling, not gradient training — matches MVP tier).
   - Success rate tracked per round on held-out variants (seeds/perturbations the improvement never saw) → `metrics.json`.

4. **Transfer run**
   - Improved agent (same harness + distilled memory) back on the real site via A's checker. The demo's green ending.

5. **Stretch:** short GRPO run on a small model for one task — only after the above works.

### Open decisions for C (proposed — confirm at kickoff)

- Memory format: a single `skill_notes.md`-style doc injected into the system prompt (simplest, demo-friendly).
- Held-out split: fix a set of eval seeds at kickoff; improvement rounds may only see training seeds.
- Concurrency: `asyncio` + per-rollout browser contexts against one env instance (sessions make this safe).

---

## Timeline and checkpoints

- **H1 — contracts set:** all four artifact stubs in `/fixtures`; target site + failure mode locked (A); B's skeleton boots.
- **H4 — env runs:** mini-env serves the failing component (verbatim path OK); C's runner completes episodes against it.
- **H6 — full loop:** real trajectory → spec → env → repro → first improvement round, end to end. If this slips, cut to MVP scope immediately.
- **H8 — freeze:** no new features; fixes, transfer run, fallback recording.
- **H10 — demo:** rehearsed twice, fallback cued.

## Kickoff agenda (first 30 min, all three)

1. Lock target site + failure mode (A proposes after hour-1 testing; B needs it for codegen).
2. Confirm the four contract shapes above — especially `env_spec.json` extras and the `/reset` session flow.
3. Confirm agent + model (same everywhere, including transfer).
4. A sends B a raw captured component early — before the full spec — so codegen prompts can start.

## Demo runbook (~90s)

1. Failure (15s) — replay trajectory on real site.
2. Env appears (15s) — spec + mini-env side by side with real site.
3. Red repro (10s) — agent fails the same way; show verifier reading DB state.
4. Fix loop (20s) — success-rate chart climbing (1/10 → 8/10).
5. Green on real site (15s) — improved agent completes the original task live.
6. Close (15s) — Devin session timeline; "Every failure becomes a test. Every test becomes training signal."
