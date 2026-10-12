#!/usr/bin/env bash
# Start the claude-openai-server live service (RichardAtCT wrapper, port 8910).
# Usage: ./start.sh   (foreground) — or run under a service manager.
set -euo pipefail
cd "$(dirname "$0")/vendor/claude-code-openai-wrapper"
set -a; . ./.env; set +a
exec .venv/bin/python -m uvicorn src.main:app --host 127.0.0.1 --port 8910
