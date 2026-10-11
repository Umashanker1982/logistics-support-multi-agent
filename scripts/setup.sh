#!/usr/bin/env bash
# macOS / Linux setup
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
[ -f .env ] || cp .env.example .env
python -m pytest -q
python -m eval.benchmark
echo "Ready. Try: python main.py demo"
