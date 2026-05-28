#!/usr/bin/env bash
# Run the tmux-based session-trace regression suite.
#
# Usage:
#   scripts/run_trace_tests.sh                 # fast tests only
#   scripts/run_trace_tests.sh --run-slow      # include plan/subagent/mcp
#   scripts/run_trace_tests.sh -k prompt       # one scenario group
#
# Prereqs checked at runtime: tmux, claude CLI, Flask API on 127.0.0.1:8321.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PYTHON="${PYTHON:-$REPO_ROOT/.venv/bin/python}"

if [ ! -x "$PYTHON" ]; then
  echo "error: python not found at $PYTHON (set PYTHON=... to override)" >&2
  exit 1
fi

# If the API isn't up, start it in the background for the duration of the run.
if ! curl -sf http://127.0.0.1:8321/api/sessions >/dev/null 2>&1; then
  echo "[trace-tests] starting Flask server in background..."
  "$PYTHON" cli/regin.py serve >/tmp/regin_trace_server.log 2>&1 &
  SERVER_PID=$!
  trap 'kill $SERVER_PID 2>/dev/null || true' EXIT
  # Poll for readiness (~15s budget).
  for _ in $(seq 1 30); do
    if curl -sf http://127.0.0.1:8321/api/sessions >/dev/null 2>&1; then
      break
    fi
    sleep 0.5
  done
fi

exec "$PYTHON" -m pytest tests/trace/ -v --tb=short "$@"
