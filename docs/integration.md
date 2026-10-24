# A → B → C integration check — 2026-10-24

Pulled teammate work through `b741fe1` from `origin/main`. The original 51 tests
passed, but the combined toolshop flow did not work. After the fixes below, a
fresh **scripted browser run passed through capture, localization, generation
fallback, reproduction, improvement, and real-site transfer**.

During publication, `ba7a4a7` added a dashboard redesign. The integration was
rebased onto it, preserving its KPI strip, charts and episode matrix while
using the matched run manifest. The redesigned dashboard was rechecked in
Chromium, including episode-cell interactions.

This is a working integration rehearsal. It is **not evidence of LLM learning**:
`browser:auto` switches from the page-1 strategy to full pagination when its
memory contains “paginate”. The distiller used the deterministic heuristic.
No Anthropic or OpenAI credentials were present, so live LLM codegen,
LLM distillation, and `browser:llm` behavior remain unverified.

## Measured results

| Stage | Actual result |
| --- | --- |
| Real-site baseline | `browser:auto` without memory chose Slip Joint Pliers ($9.17); checker expected Washers ($3.55), so the task failed |
| Localization | Rebuilding the spec from this fresh trajectory produced the same spec as `fixtures/env_spec.json` |
| Generation | Verbatim fallback loaded A's actual reference HTML/JS, passed syntax/HTTP checks, and rendered in Chromium |
| Sandbox reproduction | 0/3 passes on seeds 0–2; all three failures were wrong cart mutations after reading only page 1 |
| Training | 0/3 passes before memory; failure feedback produced the pagination note |
| Held-out evaluation | 3/3 passes on seeds 100–102, with popup enabled and label set 3 |
| Real-site transfer | Same `browser:auto` strategy plus saved memory added Washers ($3.55); A's live checker confirmed the cheapest of 50 API products |
| Dashboard | All seven stages loaded from this run; current env screenshot loaded; no JavaScript errors |
| Regression suite | `RUN_BROWSER_TESTS=1 .venv/bin/python -m pytest env/tests loop/tests -q`: **61 passed** |

The real-site browser saw 45 displayed products over five pages; the checker
compared against 50 API records. The selected item was the minimum in both.
This small sample verifies integration, not a statistically established success rate.
The additional three task variants were checked with the HTTP simulator, not
the scripted browser strategy (which explicitly supports only `shop-cheapest`).

Machine-readable results: [`integration-results.json`](integration-results.json).
Raw local artifacts, browser screenshots and session databases are in the
gitignored `.integration/` directory.

## Failures found and fixed

1. **A and C used different domains.** A captured the toolshop, while C's
   tasks and B's verifiers queried flights. `/verify` on the shop spec raised
   `sqlite3.OperationalError: no such table: flights`. Toolshop specs now carry
   their four tasks. B and C both consume those tasks; the flights fixture
   remains available for existing commands and tests.
2. **Generation produced a false success.** Repository-relative capture paths
   were treated as literal markup/JavaScript. The fallback displayed
   `fixtures/captured/component.html` instead of a product grid. Paths now
   resolve relative to the spec, repository, or env package, missing files
   fail, prompts receive source contents, and fallback JS is syntax checked.
3. **The browser and transfer adapters were stubs.** C now invokes A's
   browser strategies with an explicit entry URL. Relative links resolve
   against the page's base URL. The real-site checker runs while the browser's
   cart context is still open, and its verdict reaches C's transfer artifact.
4. **The reconstructed component lacked A's DOM hooks.** Product cards now
   preserve the selectors used by the same strategies on the real site;
   numeric cart quantity defaults to 1, server-side validation rejects invalid
   quantities, and shop labels replace flight labels. Popup dismissal works
   on both the listing and detail pages.
5. **Repro evidence lost pagination.** Request logs dropped query strings.
   Logs now preserve `?page=N`; repro requires an actual page-1 mutation and
   excludes execution errors. Distillation no longer infers pagination from
   infrastructure errors.
6. **Verifier shortcuts could pass.** A detail visit after a mutation used to
   count. Visits must now precede the mutation. Shop verification requires
   exactly one matching cart item at quantity 1. Browser agents are confined
   to their session routes and cannot browse `/state` or other sessions.
7. **Baseline cache keys omitted relevant inputs.** Keys now include the
   spec, full task, and perturbation overrides, and expired sessions cause a
   rerun. This avoids reusing flight results or ignoring evaluation knobs.
8. **Dashboard artifacts came from unrelated runs.** It now reads C's artifact
   directory and run manifest. The manifest identifies the spec, task set,
   agent mode, generation report, baseline trajectory and screenshot.
   Transfer is explicitly labeled real or rehearsal. Missing manifests do not
   combine archived flight metrics with toolshop captures.
9. **A's LLM action plumbing contained browser errors.** Fixed a Playwright
   argument call, an invalid DOM `querySelector` selector, and product navigation;
   the prompt now carries observation history. These changes support the
   interface but do not constitute a live LLM validation.

## Reproduce the connected browser run

Run all commands from the repository root. This uses a public automation demo
shop, adds one item to a disposable browser cart, and does not check out.

```bash
python3 -m venv .venv
.venv/bin/pip install -r env/requirements.txt -r loop/requirements.txt -r capture/requirements.txt
.venv/bin/python -m playwright install chromium
```

Capture the baseline. Exit status **1 is expected** when its checker records
the intended wrong-cart failure; check the trajectory rather than swallowing
all command errors.

```bash
.venv/bin/python -m capture.run_agent --strategy auto --run-name baseline \
  --out .integration/trajectory.json --media-dir .integration/media

.venv/bin/python -m capture.localize --trajectory .integration/trajectory.json \
  --out .integration/env_spec.json
.venv/bin/python -m env.generate .integration/env_spec.json \
  --component-dir .integration/component --verbatim
```

Start B's server in a separate terminal:

```bash
ENV_SPEC=.integration/env_spec.json \
ENV_COMPONENT_DIR=.integration/component \
ENV_SESSIONS_DIR=.integration/browser-sessions \
PORT=5012 .venv/bin/python -m env.app
```

Then run C with the same spec and shared browser strategy:

```bash
.venv/bin/python -m capture.shoot_env --env-url http://127.0.0.1:5012 \
  --out .integration/env.png --seed 42

LOOP_ARTIFACTS=.integration/browser-artifacts \
.venv/bin/python -m loop.demo \
  --spec .integration/env_spec.json --task shop-cheapest --agent browser:auto \
  --env-url http://127.0.0.1:5012 --trajectory .integration/trajectory.json \
  --generate-report .integration/component/generate_report.json \
  --env-screenshot .integration/env.png \
  --repro-seeds 0-2 --train-seeds 0-2 --eval-seeds 100-102 \
  --rounds 1 --real-transfer --clean

PORT=8012 .venv/bin/python dashboard/serve.py
```

Open
`http://127.0.0.1:8012/dashboard/?artifacts=.integration/browser-artifacts`.
The CLI exits nonzero if reproduction, the final evaluation, or transfer fails.
For a local rehearsal, omit `--real-transfer`; the transfer stage will say
**sandbox rehearsal**, not real-site transfer.

For a fast offline integration check using A's committed spec:

```bash
ENV_COMPONENT_DIR=fixtures/captured \
ENV_SESSIONS_DIR=.integration/sim-sessions \
LOOP_ARTIFACTS=.integration/sim-artifacts \
.venv/bin/python -m loop.demo --in-process --spec fixtures/env_spec.json \
  --task shop-cheapest --train-seeds 0-3 --eval-seeds 100-103 \
  --repro-seeds 0-3 --rounds 1 --clean

RUN_BROWSER_TESTS=1 .venv/bin/python -m pytest env/tests loop/tests -q
```

## Remaining boundaries

- A's fallback component is a **hand-written recreation** of the Angular
  interaction; “verbatim” means copying that supplied reference, not cloning
  the production Angular runtime. Real captured markup remains separate.
- LLM generation and the autonomous improvement claim need a credentialed run
  with `browser:llm`, the same capture model, and honest repro/evaluation results.
  No successful LLM run is claimed here.
- `/state` remains a trusted runner API. Browser route restrictions protect
  the supplied harness; this is not a sandbox for arbitrary hostile clients.
- The public practice API can be flaky; the live run passed during this check.
- `capture/record_fallback.sh` captures scripted reference trajectories only.
  Use the commands above for the connected learning-loop rehearsal.
