# Genuine Gemini experiment — 2026-10-24

Gemini API integration, LLM component generation and sandbox memory improvement
are working. **Real-site transfer is not yet successful.** Do not use the earlier
scripted green transfer as evidence for this learned agent.

Latest artifacts: `.integration/gemini-demo-v3/` (local, gitignored).
Model: `gemini-3.5-flash-lite`, temperature 0.7, 30-step budget, observation
policy `toolshop-observe-act-v3`. The model and observation policy were fixed
across this run's baseline, training, evaluation and transfer.

| Stage | Measured result |
| --- | --- |
| Real-site baseline | 3/3 genuine task failures; no execution errors |
| Component generation | Generated on attempt 1; 18.63s including browser checks |
| Browser fidelity probes | Seeds 42 and 101 passed pagination, seeded ordering and cart verification |
| Sandbox repro | 3/3 matching pagination failures |
| Held-out baseline | 0/5, seeds 100–104 |
| Training | Ordinary sampling found no success; one LLM-proposed memory produced 2/3 verified training successes |
| Memory distillation | Full task + successful actions/observations → saved generic skill notes |
| Held-out evaluation | 5/5 on the same seeds and perturbations; no provider errors |
| Real-site transfer | FAIL: selected Slip Joint Pliers from page 1; checker expected Washers ($3.55) |
| Total experiment runtime | 600.19s |

The learned memory explicitly advises examining every listing page and comparing
prices before selecting. Its hash matches the memory delivered during real
transfer. The real site's page-number observation was also confirmed correct.
This is evidence of sandbox improvement and an unresolved transfer gap, not
proof of robust learning or successful real-world transfer. Five evaluation
cases are a small sample.

## Preserved earlier attempts

- `.integration/gemini-demo`: natural failure, generated component and repro
  worked; free-tier HTTP 429 errors invalidated evaluation. Interrupted.
- `.integration/gemini-demo-v2`: valid 0/5 → 2/5 → 3/5 sandbox curve, but both
  real transfers failed. A real/sandbox pagination-label mismatch was diagnosed.
- `gemini-demo-v3` uses normalized visible page numbers and retained observations
  for distillation. It starts a fresh baseline; curves from different observation
  policies are never combined.

API requests are paced at 14/minute to fit the observed 15/minute free-tier
limit. Bounded retries honor Google's retry delay. Unresolved provider errors
invalidate evaluation rather than counting as agent-learning evidence.

## Demo and next work

Serve the repo with `dashboard/serve.py`, then open:

```text
http://127.0.0.1:8012/dashboard/demo.html?artifacts=.integration/gemini-demo-v3
http://127.0.0.1:8012/dashboard/?artifacts=.integration/gemini-demo-v3
```

The demo can truthfully show a real LLM failure, generated sandbox, matching
reproduction, verified memory improvement and the failed transfer. The remaining
acceptance gate is reliable real-site transfer. Investigate the remaining
observation/context differences and training diversity before claiming that gate.
Any further observation-policy change requires a fresh baseline.

Implementation validation: 91 tests passed, including Chromium, provider
behavior, observation normalization, verified exploration, and continuation with
an unchanged evaluation fingerprint. The teammate's OpenAI-compatible endpoint
support and compact demo layout were integrated.

The experiment above predates the later teammate merge through `51d04c5`.
That merge adds structured reproduction evidence, safer restored configs,
invalid-action feedback and the ReEnvision frontend. The combined LLM adapter is
version `toolshop-observe-act-v4`; the scripted adapter remains v2 for the
bundled rehearsal. The recorded v3 results are preserved as historical evidence.
They cannot be resumed under a different policy version; new experiments must
establish a fresh baseline.

The final integrated regression suite passed **103 tests**, including Chromium.
