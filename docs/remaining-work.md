# Remaining work: a credible Failure-to-Env browser demo

Plan started 2026-10-24 from `fda640c`. The scripted toolshop integration works.
This plan closes the gap between that rehearsal and the vision's actual
LLM-agent failure → sandbox → memory improvement → real-site transfer.

Implementation is now present for the items below. The command reference is
[`demo-runbook.md`](demo-runbook.md). Live LLM acceptance remains blocked on
credentials and the empirical gates listed at the end; implementation readiness
must not be confused with a demonstrated model improvement.

## Definition of done

One unchanged browser-agent implementation/model attempts a mutation task on
the real toolshop, fails naturally, fails the same way in a generated sandbox,
and improves through memory distilled from verified training successes.
Baseline and each round are measured on an identical held-out evaluation set.
The saved memory is transferred back to the real site and verified against
the live cart. Every claimed outcome has a browser recording and machine-readable
evidence. No gradient training or RL claim is needed for this MVP.

## Implementation order

| Work | Implementation | Acceptance criterion |
| --- | --- | --- |
| Freeze the agent | Shared provider/model/temperature/step-budget configuration, recorded with every episode | Capture, repro, training, evaluation and transfer use the same configuration |
| Record browser episodes | Screenshots, DOM, action history and video; headed baseline/transfer option | A viewer can replay both the failure and the successful transfer |
| Measure a fair curve | Round-0 evaluation, fixed seeds/knobs, sample counts and uncertainty | Evaluation inputs match at every point; transfer and repro are excluded from the curve |
| Learn from evidence | Multiple uncached attempts per training seed; distill only verified successes | Evaluation records never enter distillation; zero successes leaves memory unchanged; provider errors are not heuristic fallbacks |
| Validate generated behavior | Chromium checks for rendering, pagination, detail navigation and persisted cart mutation | Broken generated JavaScript cannot pass on HTTP status alone |
| Orchestrate the real experiment | Baseline sampling → localize → generate → repro → improve → transfer, with stage reports | The command stops honestly at failed prerequisites; it never deliberately weakens the baseline to force the story |
| Present the demo | Recorded browser panels, fixed-evaluation chart, generation provenance, run status | Scripted/LLM and recorded/live claims are explicit; no unrelated archived artifacts are mixed |
| Validate and hand off | Offline policy tests, HTTP/Chromium tests, reproducible commands, devlog | All unblocked checks pass; credentialed results are reported separately |

The existing `loop.demo` remains the scripted rehearsal. A separate
`loop.experiment` command enforces the genuine LLM experiment's evidence gates.
The browser and verifier code are shared.

## Experiment protocol

- Start from a fresh output directory and empty memory.
- Sample three real-site baseline episodes; require at least two genuine
  task failures. Save every attempt, including successes and execution errors.
- Keep the task, model, sampling settings, tools and step budget fixed. If
  the baseline already succeeds, report that the candidate is unsuitable.
- Build the spec from one recorded failure. Localization is deterministic for
  this domain and must be disclosed. Try LLM component generation first;
  supplied-reference fallback is allowed only as an explicit run choice.
- Check generated browser behavior before starting rollouts. Mechanical
  generation probes do not count as agent-learning evidence.
- Confirm matching sandbox failures with the same LLM and no skill memory.
- Use training seeds 0–4 by default, two independent attempts per seed per
  round, and evaluation seeds 100–109. Training and evaluation are disjoint.
  Freeze the evaluation perturbations for baseline and every round.
- Distill successes from training only. If there are none, keep the memory
  and report zero evidence. Do not teach the hard-coded pagination heuristic
  in the LLM experiment. Never cache stochastic LLM episodes.
- Optional `--explore` bootstraps successful trajectories: the LLM proposes
  one memory candidate from failed training traces (without verifier answers),
  and the same browser agent tests it on training seeds. Only successful
  executions can then inform the saved memory. Failed hypotheses are retained
  as artifacts but never promoted; eval data remains excluded.
- Record memory snapshots, source episode IDs, evaluation rates and counts.
  Report regressions and flat curves as observed.
- Success distillation receives the full task instruction alongside verified
  traces, so its rules retain the decision and stopping criteria that explain
  success. Completed rounds can be continued with `loop.continue_experiment`;
  the original evaluation fingerprint and all earlier transfer attempts remain.
- Transfer only when saved memory has evidence and final evaluation improved.
  A transfer success is a separate one-task result, not another curve point.

## Demo rehearsal (~90 seconds)

1. 15s: show the real baseline browser choosing the wrong cart item and its
   verifier result.
2. 15s: show the captured interaction becoming a resettable mini-env and the
   same baseline failure reproduced there.
3. 20s: replay actual training/evaluation browser footage, reveal the memory
   change, and show the fixed-evaluation curve with denominators.
4. 30s: show the same agent with saved memory succeeding on the real site,
   either live in a headed browser or as a clearly labeled recording.
5. 10s: close on the real checker result and Devin session timeline.

Precompute training. Keep the real baseline and transfer recordings ready as
fallbacks. Do not promise a thirty-second generation time until measured.

## External dependency / live acceptance

At the start of this work, neither `ANTHROPIC_API_KEY` nor `OPENAI_API_KEY`
is configured in the shell. Keys must be configured locally; values must not
be pasted into documentation or artifacts. The implementation can be validated
offline, but the following remain empirical gates requiring credentials:

- a naturally failing LLM baseline on this task;
- successful LLM generation with preserved behavior;
- verified exploratory successes sufficient to teach a useful skill;
- improved evaluation performance and real-site transfer.

The toolshop may be too easy for the selected model. If it is, report the
result and select another task/failure interaction without fabricating a
failure or changing the agent between baseline and evaluation.

## Validation completed

72 tests pass, including Chromium recordings, the strict protocol, model
configuration, and generation behavior checks. A fresh recorded **scripted**
rehearsal produced a comparable 0/3 → 3/3 → 3/3 curve and passed live transfer.
See [`demo-validation.json`](demo-validation.json). Preflight confirms browser
and Node availability and the missing-credentials blocker. The live LLM gates
above remain open; no such run has been reported as successful.
