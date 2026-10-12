#!/usr/bin/env bash
# Start the claude-openai-server live service (RichardAtCT wrapper).
# Host/port come from vendor/claude-code-openai-wrapper/.env
# (CLAUDE_WRAPPER_HOST, PORT). Currently 0.0.0.0:8910 — LAN-reachable,
# bearer-token gated (API_KEY in the same .env).
set -euo pipefail
cd "$(dirname "$0")/vendor/claude-code-openai-wrapper"
set -a; . ./.env; set +a
exec .venv/bin/python -m uvicorn src.main:app --host "${CLAUDE_WRAPPER_HOST:-0.0.0.0}" --port "${PORT:-8910}"
