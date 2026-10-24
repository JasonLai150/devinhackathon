# Browser demo and genuine LLM experiment

The demonstration has three visual anchors: a browser failure, a measured
evaluation curve, and a browser success with saved skill memory. The generated
environment and matching sandbox failure connect those anchors to the vision.

The dashboard defaults to the portable scripted bundle at `fixtures/mvp-demo`.
Generate a fresh connected recording with
`python -m loop.rehearse --out .integration/rehearsal --real-transfer`.
All output folders must be under the repository to be served by the dashboard.

There are two explicitly different modes:

- `loop.experiment`: the genuine LLM protocol. It refuses missing credentials,
  unsuitable baselines, broken generation, unverified reproduction, or unsupported
  improvement claims. Model/provider/temperature/tools/step limit are fixed.
- `loop.demo`: the scripted rehearsal. It validates presentation and infrastructure;
  its heuristic memory switch must not be called LLM learning.

## Setup and preflight

From the repository root:

```bash
.venv/bin/pip install -r capture/requirements.txt -r env/requirements.txt -r loop/requirements.txt
.venv/bin/python -m playwright install chromium
.venv/bin/python -m loop.experiment --preflight --out .integration/llm-demo
```

Configure `GEMINI_API_KEY`, `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` in the local
environment, or put one in the gitignored `.env.local` file:

```dotenv
GEMINI_API_KEY=your-key-here
```

Load that file explicitly; its values are never printed or put in artifacts:

```bash
.venv/bin/python -m loop.experiment --env-file .env.local --provider gemini \
  --preflight --out .integration/gemini-run
```

Existing environment variables take precedence over the file. Gemini uses the
Google AI Studio `generateContent` API with the key in a request header.
The default is `gemini-3.5-flash-lite`; `--model` selects another model available
to your project. Gemini 3.6+ uses provider-default sampling, recorded as
`temperature: null`, because these models ignore custom sampling controls.
Earlier Gemini models retain the configured temperature.
`GOOGLE_API_KEY` is also accepted as a Gemini key alias.
Do not put keys in artifacts, docs, commands committed to Git, or chat.
Preflight reports availability only and makes no model requests. A missing
credential returns exit status 2 and writes `preflight.json`, not fake rollouts.

To verify API access as well, make one small request to the configured model:

```bash
.venv/bin/python -m loop.experiment --provider anthropic \
  --preflight --check-provider --out .integration/claude-check
```

This check has a 16-token output limit for Anthropic/OpenAI or 256 for Gemini
(whose response budget can include thinking). Actual experiment commands perform it
automatically before opening browsers. HTTP failures retain the provider's
error type/message and request ID with credentials redacted, so account,
model-access and request problems can be diagnosed. If a browser episode later
encounters an execution error, the experiment stops with `status: error`;
that is not evidence that the task was solved or that a harder task is needed.

## Run the genuine experiment

```bash
.venv/bin/python -m loop.experiment \
  --out .integration/llm-demo \
  --provider openai --model gpt-4o-mini --temperature 0.7 \
  --max-steps 30 --baseline-attempts 3 --minimum-failures 2 \
  --train-seeds 0-4 --eval-seeds 100-109 --repro-seeds 0-2 \
  --attempts 2 --rounds 2 --concurrency 2 --headed
```

Use a fresh output directory for each experiment. The example uses the
existing default OpenAI model; use `--provider anthropic --model <model>` if
that is your configured provider. Model capability and baseline failure rate
must be measured, not assumed.

For Gemini, use `--env-file .env.local --provider gemini` instead of the
OpenAI provider/model options. The same frozen configuration is used for
capture, code generation, training, distillation, evaluation and transfer.
API availability and quota are checked before browser work. Use
`--preflight --check-provider` for a standalone API check; ordinary preflight
only checks local configuration.
For Gemini, the supported low-cost default is `gemini-3.5-flash-lite`:

```bash
.venv/bin/python -m loop.experiment --provider gemini \
  --model gemini-3.5-flash-lite --out .integration/gemini-demo-v2 \
  --headed --concurrency 1 --explore
```

Gemini uses Google's `generateContent` REST endpoint, with the key in an
`x-goog-api-key` header. The same provider is used for browser actions, component
generation and memory distillation. Provider errors retain Google's status
code/message with credentials redacted; incomplete or blocked answers are
execution errors. Use `--preflight --check-provider` to check model access first.

The tested key's free tier allows 15 requests/minute. Gemini requests are paced
at 14/minute across all workers by default (`GEMINI_RPM` overrides that budget).
Google's bounded retry delay is honored for up to two retries on 429/503 errors.
An unresolved provider error invalidates an evaluation point and stops the
strict experiment; it must not appear as a policy failure on the curve.

`--explore` adds a bounded bootstrap when ordinary training attempts yield no
successes. The same LLM proposes one memory hypothesis from unsuccessful
training action traces, with verifier answers omitted. That candidate is tested
once per training seed. The hypothesis itself is never saved as accepted memory:
only verified successful executions enter the normal success distiller. If the
candidate fails, the accepted memory stays unchanged. Proposal files and source
episodes are retained in `rounds/`. Evaluation episodes never inform proposals.

`--headed` shows baseline and real-site transfer in Chromium windows.
Training/evaluation stay headless and are recorded. `--slow-mo 200` slows
visible Playwright interactions for a presentation without changing the
agent's action/step budget. Every browser episode writes a WebM video,
screenshots, DOM snapshots and a trajectory with model configuration.

The command does the following:

1. Samples real-site baseline attempts without memory and retains all attempts.
   A task failure counts only if the agent executed and the checker rejected
   its cart. Infrastructure errors do not satisfy the failure requirement.
2. Builds a deterministic toolshop spec from a recorded failure (disclosed
   localization scope), then attempts LLM generation of the interaction.
3. Exercises the component in Chromium: paginated data must render in seeded
   order, all pages must be reachable, and the detail/cart flow must write a
   correct mutation. These are mechanical checks, separate from LLM evidence.
4. Reproduces the failure with the same LLM in the sandbox.
5. Measures a **round-0 held-out baseline**, then samples fresh training
   episodes. Only verified training successes enter the distillation prompt.
   With `--explore`, a single proposed memory can bootstrap those successes
   through additional training attempts. No verified successes still means no
   accepted memory change. API errors do not trigger a heuristic.
6. Re-evaluates the exact same held-out seeds and perturbations each round.
   LLM episodes are never cached. Snapshots record which successful episodes
   contributed to each memory revision.
7. Attempts real transfer only after memory has successful evidence and the
   final evaluation improves over the held-out baseline. The live checker
   requires exactly one matching product at quantity 1.

By default, generation failure stops the experiment. `--allow-verbatim`
explicitly permits A's hand-recreated reference component as fallback. The
manifest and dashboard retain that provenance; it is not described as generated
code when the LLM did not produce it.

The first full command can involve dozens of browser episodes and many model
requests. Precompute it before the presentation. Default bounds are three
baseline attempts, three repro episodes, two rounds of ten training attempts,
and ten evaluation episodes at round 0 and each later round, plus transfer.

Reproduction now requires structured evidence from both sides: a checker-
confirmed wrong choice, only page one observed, the chosen item on that page
and the correct item unseen. Wrong quantities, empty carts, execution errors
and missing/older trajectories cannot establish matching failures. A sandbox
diagnostic is reported separately from a confirmed real-site reproduction.

## Restore an improved agent

```bash
.venv/bin/python -m loop.transfer \
  --config .integration/gemini-run/agent_config.json --env-file .env.local \
  --out .integration/gemini-transfer.json \
  --rollouts .integration/gemini-transfer.jsonl \
  --metrics .integration/gemini-transfer-metrics.json
```

The config carries the original task, provider/model/temperature, step budget
and browser policy version. Memory and a spec snapshot use paths relative to
the config and are protected by hashes. Keep those files together when copying
a run. Missing files, changed memory/spec or a different browser policy cause
an error before the browser starts. Config-driven transfer does not accept
agent/task/spec/memory overrides. The old archived configs must be regenerated.

## Recorded presentation

To add learning rounds after a completed round (including a failed transfer),
retain the original baseline, component and evaluation set:

```bash
.venv/bin/python -m loop.continue_experiment .integration/gemini-demo-v2 \
  --rounds 1 --attempts 1 --concurrency 1 --headed --explore
```

Continuation restores the original provider/model/temperature/tools, task,
training seeds and held-out seeds from the artifacts. It rejects a changed
agent or evaluation configuration. It appends evaluation points, memory
snapshots and rollouts instead of replacing the baseline. Earlier transfer
attempts remain in `transfer_history.json` and the episode matrix. During
training, the dashboard labels the previous transfer video as a previous attempt.
Incomplete/interrupted rounds require a fresh run rather than silently overwriting
partial evidence.

```bash
PORT=8012 .venv/bin/python dashboard/serve.py
```

Open:
`http://127.0.0.1:8012/dashboard/?artifacts=.integration/llm-demo`

The dashboard provides baseline/transfer video players, episode-cell replay,
the final memory, generation provenance and run status. Reload to view a run
still in progress. All videos are labeled recordings. Headed Chromium is the
live demonstration surface; do not represent video playback as live execution.

The chart uses only evaluation metrics with the same evaluation fingerprint
and a round-0 evaluation. It shows sample sizes and 95% Wilson intervals.
Repro and one-off real transfer results never become curve points. An archived
run lacking a comparable baseline is explicitly shown without a learning curve.

## Artifacts and failure states

| Artifact | Evidence |
| --- | --- |
| `run_manifest.json` | stage/status, fixed agent settings, spec, generation provenance, selected baseline |
| `baseline_trials.json` | all real-site baseline outcomes, including errors and successes |
| `component/generate_report.json` | attempts, rejected candidates, browser fidelity results, elapsed time |
| `repro_report.json` | matching failure evidence from session request logs and mutations |
| `rollouts.jsonl` | train/eval/repro episodes and paths to recordings |
| `metrics.json` | fixed-evaluation fingerprint, seeds, knobs, successes, counts, intervals |
| `rounds/round-N.md` and `.json` | memory snapshot and successful training episode IDs |
| `agent_config.json` | saved agent configuration and memory location |
| `transfer_result.json` | verified real-site result, action trace and recording paths |

`blocked` means an empirical prerequisite failed; the results are retained.
`error` means execution failed. `complete` means the observed run improved and
transferred; it does not establish statistical significance or general learning
from a small sample. The managed sandbox shuts down at the end; replay artifacts
remain usable.

## Scripted rehearsal without credentials

The shortest complete path is:

```bash
.venv/bin/python -m loop.rehearse --out .integration/rehearsal --real-transfer
```

This manages capture, reference-component validation, an isolated HTTP server,
rollouts and recordings. It requires a fresh output directory. Its manifest
retains execution errors and clearly labels the scripted mode and reference
fallback. The served default bundle is `fixtures/mvp-demo`.

For separate stage commands, use [integration.md](integration.md), adding:

```text
capture.run_agent: --video
loop.demo: --record-dir .integration/demo-ready/episodes
```

Choose `.integration/demo-ready` as `LOOP_ARTIFACTS` and point the dashboard
there. The rehearsal now also measures round 0 and each later round on the
same held-out seeds, but its scripted behavior is still labeled as such.
`loop.demo` directs LLM runs to `loop.experiment` instead of silently using the
scripted distillation path.

## Presentation sequence (~90 seconds)

1. Play the real failure and show the incorrect cart/checker result.
2. Show the generated component (or disclose fallback) and its red sandbox repro.
3. Show measured evaluation points and the memory snapshot that changed.
4. Show the improved browser on the real site, live or clearly recorded.
5. Finish with the actual checker verdict and Devin session timeline.

Never weaken the baseline or swap models/tools between phases to manufacture
improvement. If this model already solves the candidate task, use the retained
baseline results to select a different candidate. If no useful successes are
found, report the flat curve and collect more training attempts.
