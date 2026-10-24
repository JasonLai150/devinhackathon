#!/usr/bin/env bash
# Fallback recording: regenerate every artifact the dashboard reads,
# deterministically (scripted strategies — no LLM key needed).
#
#   bash capture/record_fallback.sh
#
# Produces/refreshes:
#   fixtures/trajectory.json           real failure run (naive strategy)
#   fixtures/trajectory_transfer.json  successful run (smart strategy)
#   fixtures/shots/<run>/*.png + fixtures/dom/<run>/*.html  per-step media
#   fixtures/video/<run>/*.webm        playwright video (demo footage)
#   fixtures/captured/                 component html/js + real markup
#   fixtures/env_spec.json             spec built from the trajectory
#   fixtures/shots/env/index.png       mini-env screenshot
set -euo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python

echo "=== 1/5  failure run on the real site (naive)"
$PY -m capture.run_agent --strategy naive --run-name naive \
    --out fixtures/trajectory.json --video || true   # run "fails" by design

echo "=== 2/5  capture component + localize spec"
$PY -m capture.capture_component
$PY -m capture.localize

echo "=== 3/5  env screenshot (env on the real spec)"
$PY -m capture.shoot_env --seed 42

echo "=== 4/5  transfer run on the real site (smart = improved agent)"
$PY -m capture.run_agent --strategy smart --run-name smart \
    --out fixtures/trajectory_transfer.json --video

echo "=== 5/5  done — dashboard reads cached artifacts"
echo "serve:  $PY dashboard/serve.py   -> http://localhost:8000/dashboard/"
