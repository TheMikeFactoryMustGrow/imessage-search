#!/usr/bin/env bash
# Same gate as intended GitHub Actions job (R-TEST-01).
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m venv .venv
.venv/bin/pip install -q -r requirements-dev.txt
.venv/bin/coverage run -m pytest -q
.venv/bin/coverage report -m
