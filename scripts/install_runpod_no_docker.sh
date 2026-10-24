#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 --version
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip wheel
python -m pip install -r requirements.txt
python -m voice_agent.cli validate
printf '\nInstall complete. Edit .env and config/agent.yaml, then:\nsource .venv/bin/activate && bash scripts/run_local.sh dev\n'
