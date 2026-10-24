#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [ ! -f .env ]; then
  echo 'Missing .env. Run: cp .env.example .env, then configure credentials.' >&2
  exit 1
fi
python - <<'PY'
from dotenv import load_dotenv
import os
load_dotenv()
for key in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"):
    if not os.environ.get(key):
        raise SystemExit(f"Required variable {key} missing from .env")
PY
# worker.py loads the environment using python-dotenv, so it is not necessary
# (or safe) to source arbitrary .env text in a shell.
exec python -m voice_agent.worker "${1:-dev}"
