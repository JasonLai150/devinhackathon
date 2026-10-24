# devinhackathon

Failure-to-Env: turn browser-agent failures into resettable training environments.

- [Vision](vision.md) and [implementation plan](PLAN.md)
- [Connected A → B → C run: setup, validation, and current limitations](docs/integration.md)
- [Implementation log](docs/devlog.md)
- [Remaining work and acceptance gates](docs/remaining-work.md)
- [Browser demo / LLM experiment runbook](docs/demo-runbook.md)

The integrated toolshop demo uses A's browser strategies, B's session-scoped
environment, and C's rollout/memory loop. The scripted browser rehearsal has
been verified through a real-site cart mutation. A live Gemini run validated
LLM generation and matching failure reproduction, but produced no verified
training successes; autonomous improvement remains unproven. See
`docs/demo-validation.json` for the measured results.

## Run the MVP

```bash
python3 -m venv .venv
.venv/bin/pip install -r env/requirements.txt -r capture/requirements.txt -r loop/requirements.txt
.venv/bin/python -m playwright install chromium
RUN_BROWSER_TESTS=1 .venv/bin/python -m pytest env/tests loop/tests -q
.venv/bin/python dashboard/serve.py
```

Open `http://localhost:8000/dashboard/`. The default is the matched, recorded
scripted toolshop bundle in `fixtures/mvp-demo/`. It is a rehearsal, not an
LLM-learning result. Rebuild the full connected browser run into a fresh folder:

```bash
.venv/bin/python -m loop.rehearse --out .integration/rehearsal --real-transfer
```

Then select `http://localhost:8000/dashboard/?artifacts=.integration/rehearsal`.
The command captures a fresh real failure, validates the supplied reference
component in Chromium, runs baseline/training/evaluation and checks the final
cart on the real practice site. It records all browser episodes and stops the
sandbox when finished.

For an LLM experiment, put a supported key in the gitignored `.env.local`.
Gemini users can set `GEMINI_API_KEY` and run:

```bash
.venv/bin/python -m loop.experiment --env-file .env.local --provider gemini \
  --preflight --out .integration/gemini-run
.venv/bin/python -m loop.experiment --env-file .env.local --provider gemini \
  --out .integration/gemini-run
```

Preflight makes no API requests. The actual experiment retains blocked/failed
results; it does not guarantee a learning curve. See the runbook for model,
sampling and budget controls.
