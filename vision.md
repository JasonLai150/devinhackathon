# Failure-to-Env: Turning Agent Failures into Training Environments

Oct 24, 2026 · @Jason Lai

## Vision

**Every web agent failure becomes a training environment.** When an agent fails on a real website, we capture the trajectory, isolate the interaction that broke it, and auto-generate a minimal, resettable sandbox that reproduces the failure, with a task and a programmatic verifier. The agent then improves inside that sandbox until it passes, and we confirm the fix transfers back to the real site.

Think of it as unit tests for agents: a regression suite that grows from real failures instead of hand-written benchmarks.

**One-line pitch:** "Your agent failed on a real site. Thirty seconds later, that failure is a reproducible, verifiable training environment, and the agent learns to beat it."

**Built with Devin:** each workstream below runs in its own Devin session(s), and we'll show the session timeline in the pitch.

## Why it matters and how it differs

Web agents need training environments that are safe, resettable, and give verifiable rewards. Live sites offer none of that. The current approach is to clone whole websites into sandboxes. Examples include [VeriEnv](https://arxiv.org/abs/2603.10505) (Georgia Tech, ICML 2026) and [Chakra's Dojo](https://www.chakra.dev/publications/product-launch-dojo).

We take a different angle:

|  | Whole-site cloning | Failure-to-Env (ours) |
| --- | --- | --- |
| Starting point | A URL | A real agent failure |
| What gets built | The full site | Only the slice that broke the agent |
| Task source | Generated broadly | Targeted at a known weakness |
| Value | Coverage | A closed improve-and-verify loop |

The story is a loop rather than a static gym: **fail → reproduce → improve → verify on the real site.**

## How it works

Seven stages run end to end. The letters in brackets mark which teammate owns each stage (see Team breakdown).

1. **Capture \[A\].** An off-the-shelf browser agent attempts a task on a real public demo site. Every step is logged: screenshot, DOM snapshot, action, URL, and the final outcome.
2. **Localize \[A\].** An LLM reads the failed trajectory and writes an env spec (JSON). The spec records which pages and UI component matter, the data involved, the step where things went wrong, and the captured HTML of the failing component.
3. **Generate env \[B\].** The spec is filled into a fixed web app template (pages + SQLite + `/reset` + `/state`). The failing component's real HTML/JS is reused so the tricky interaction behaves identically.
4. **Tasks + verifiers \[B\].** Generate the original task plus 3–5 variants. Each verifier checks DB state through `/state`, never screenshots.
5. **Reproduce \[C\].** The same agent runs on the mini-env and should fail the same way. This is the credibility moment.
6. **Improve \[C\].** N rollouts per task variant, optionally with perturbations. Verified successes are distilled into a skill note or few-shot memory, and success rate is tracked across rounds.
7. **Transfer \[C\].** The improved agent goes back to the real site and should now succeed.

The demo dashboard \[A\] shows all seven stages as one timeline.

## Demo script (\~90 seconds live)

1. **The failure (15s).** Play the agent failing a real task on the demo site, e.g. "find the cheapest item across all pages."
2. **The env appears (15s).** Show the generated spec and the mini-env side by side with the real site.
3. **Red repro (10s).** The agent fails in the mini-env the same way. Point out the verifier reading DB state.
4. **The fix loop (20s).** Show the success-rate chart climbing across rounds (e.g. 1/10 → 8/10).
5. **Green on the real site (15s).** The improved agent completes the original task live.
6. **Close (15s).** Show the Devin session timeline, then: "Every failure becomes a test. Every test becomes training signal."

Have a pre-recorded full run ready as the fallback if wifi or the live agent misbehaves.

## Scope and cut lines

One target site, one failure mode, one off-the-shelf agent. The mini-env only needs to preserve the failing interaction; it doesn't need to look like the real site.

**Locked decisions (first 30 minutes, all three of us):**

- **Target site:** a public demo site built for automation testing (sandbox shop or bookstore), with no login and no CAPTCHA.
- **Failure mode:** one that agents fail reliably. Candidates are pagination, custom dropdowns, date pickers, sort/filter state, and actions hidden behind hover or modals.
- **Agent:** Playwright + LLM, or browser-use. Same agent and model everywhere.

| Tier | Includes |
| --- | --- |
| MVP (must ship) | One real failure, spec (hand-written is fine), generated mini-env, red repro, rejection-sampling fix, green on real site, dashboard |
| Nice to have | Auto-localization from trajectory, 3–5 task variants, perturbations, anti-cheat verifier demo |
| Stretch | A second failure type to show generality; a short RL run on a small model |

If something is hand-built for the demo, say so in the pitch. Be explicit about what is automated.

## Team breakdown

Three workstreams, each runnable in its own Devin session(s) against fixtures, so nobody blocks on anyone else until integration. Each person owns their stage outputs end to end.

### Teammate A: Capture + Dashboard ("the real world")

Owns everything that touches the real site, plus the demo UI.

- [ ] Agent harness: run a task on the real site with Playwright/browser-use and log each step to `trajectory.json`
- [ ] Real-site success checker (scripted check, or LLM judge as fallback)
- [ ] In the first hour, test 3–5 candidate tasks and pick the one that fails most reliably, then share the failing trajectory
- [ ] Failure localization: LLM reads the trajectory and writes `env_spec.json` (hand-write it first, then automate)
- [ ] Demo dashboard: timeline of failure → env → repro → improvement chart → real-site success, with side-by-side screenshots
- [ ] Record the full fallback demo run

**Delivers:** `trajectory.json`, `env_spec.json`, the dashboard.

### Teammate B: Env Generator + Verifiers ("the sandbox")

Owns turning a spec into a runnable, verifiable environment.

- [ ] Env template: small web app with SQLite, seeded data, `POST /reset?seed=`, `GET /state`
- [ ] Generator: fill the template from `env_spec.json`, reusing the captured component HTML/JS
- [ ] Task generator: the original task plus 3–5 variants as `tasks.json`
- [ ] Verifiers: one Python function per task that reads `/state` and returns pass/fail plus a reason
- [ ] Perturbation knobs via seed: reordered items, extra pages, renamed labels, a popup
- [ ] Anti-cheat check: a hand-made cheating trajectory that the verifier rejects

**Delivers:** a running mini-env URL, `tasks.json`, `verifiers.py`.

### Teammate C: Rollouts + Improvement ("the learning loop")

Owns running the agent at scale and making it better.

- [ ] Rollout runner: N parallel episodes of `reset → agent → verify`, logged to `rollouts.jsonl`
- [ ] Repro check: confirm the baseline agent fails in the mini-env the same way it failed on the real site
- [ ] Improvement loop: keep verified successes, distill them into a skill note or few-shot memory, and re-run
- [ ] Metrics: success rate per round on held-out variants, exported as `metrics.json` for the dashboard
- [ ] Transfer run: the improved agent on the real site (reusing A's harness)
- [ ] Stretch: short GRPO run on a small model for one task

**Delivers:** `rollouts.jsonl`, `metrics.json`, the improved agent config.

## Interfaces between workstreams

Agree on these four contracts in the kickoff, and commit a stub of each to the repo before splitting up. Everyone builds against the stubs until real data lands.

| Artifact | Producer → consumer | Shape |
| --- | --- | --- |
| `trajectory.json` | A → C (repro comparison), dashboard | `{task, site_url, steps: [{i, action, url, screenshot_path, dom_path}], outcome, failure_step}` |
| `env_spec.json` | A → B | `{pages: [...], component: {name, html, js}, data_schema, seed_data, failure_note}` |
| Env API + `tasks.json` + `verifiers.py` | B → C | `POST /reset?seed=`, `GET /state`; `tasks: [{id, instruction, verifier, seed}]`; `verify(task_id, state) -> {passed, reason}` |
| `rollouts.jsonl` + `metrics.json` | C → dashboard | per episode `{round, task_id, seed, passed, reason, steps}`; `metrics: [{round, success_rate, n}]` |

**Repo layout:** `/capture` (A), `/env` (B), `/loop` (C), `/dashboard` (A), `/fixtures` (shared stubs). One Devin session per folder keeps PRs from colliding.

## Timeline and checkpoints

&#91;embedded content: build timeline · 3 lanes, 5 checkpoints\]

Hours are relative to kickoff; scale them to the actual event length.

- **H1, contracts set:** stubs for all four artifacts committed, target site and failure mode locked.
- **H4, env runs:** B's mini-env serves the failing component and C's runner completes episodes against it.
- **H6, full loop:** real trajectory → spec → env → repro → first improvement round, end to end. If this slips, cut to MVP scope immediately.
- **H8, freeze:** no new features; only fixes, the transfer run, and the fallback recording.
- **H10, demo:** rehearsed twice, with the fallback cued.

## Risks and mitigations

| Risk | Owner | Mitigation |
| --- | --- | --- |
| Agent doesn't fail reliably on the real site | A | Test 3–5 candidate tasks in hour 1 and pick the most consistent failure |
| Mini-env doesn't reproduce the failure | B | Copy the real component's HTML/JS verbatim instead of regenerating it |
| Fix doesn't transfer back to the real site | C | Pick a failure whose fix is a generalizable skill (e.g. "paginate before comparing"), not site-specific luck |
| Env codegen eats the day | B | Hand-build the mini-env from the template for the demo; present automated generation honestly as partial |
| Rollouts too slow for many rounds | C | Small N (5–10), short tasks, run variants in parallel, cache the baseline |
| Live demo breaks | A | Pre-recorded full run, cached results in the dashboard |
