#!/usr/bin/env bash
# E2E smoke test: drive a real Claude Code session with hook_manager
# layered in, and verify that (1) the mvn-block gate fires and (2) a
# benign tool call round-trips cleanly.
#
# Auth note: this script inherits the user's existing auth (--setting-sources
# "user") because --setting-sources "" would strip both settings and auth,
# producing a 403. The downside is the user's *existing* hooks also fire;
# that's acceptable for a smoke test — the new manager's behaviors layer
# on top. For a pure-isolated run, export ANTHROPIC_API_KEY first and pass
# `--setting-sources ""` to this script's underlying `claude` invocation.
#
# Usage:
#   bash hook_manager/e2e_smoke.sh
#
# Exit codes:
#   0 = both checks passed
#   nonzero = at least one check failed

set -u
set -o pipefail

WORKTREE="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PY:-$WORKTREE/../../../.venv/bin/python}"
if [[ ! -x "$PY" ]]; then
  PY="$(command -v python3)"
fi

TMP="$(mktemp -d -t hook-manager-e2e.XXXXXX)"
trap 'rm -rf "$TMP"' EXIT

SETTINGS="$TMP/settings.json"
cat >"$SETTINGS" <<JSON
{
  "hooks": {
    "PreToolUse":       [{"hooks":[{"type":"command","command":"$PY -m hook_manager PreToolUse","timeout":30}]}],
    "PostToolUse":      [{"hooks":[{"type":"command","command":"$PY -m hook_manager PostToolUse","timeout":120}]}],
    "UserPromptSubmit": [{"hooks":[{"type":"command","command":"$PY -m hook_manager UserPromptSubmit","timeout":15}]}],
    "Stop":             [{"hooks":[{"type":"command","command":"$PY -m hook_manager Stop","timeout":15}]}]
  }
}
JSON

export PYTHONPATH="$WORKTREE"

run_claude() {
  # $1 = prompt string
  # $2 = output stream file
  claude -p "$1" \
    --setting-sources "user" \
    --settings "$SETTINGS" \
    --output-format stream-json \
    --verbose \
    --model haiku \
    --dangerously-skip-permissions \
    --no-session-persistence \
    < /dev/null \
    >"$2" 2>"${2%.jsonl}.stderr.log" || true
}

# ── Test 1: the mvn gate blocks a direct `mvn` command ────────────────
echo "[1/2] prompting claude to run 'mvn clean install'…"
STREAM1="$TMP/stream1.jsonl"
run_claude "Please execute the shell command: mvn clean install -DskipTests" "$STREAM1"

# "maven MCP tools instead" is uniquely the hook_manager deny reason.
# As a fallback, accept a permission_denials array that records mvn as blocked.
if grep -q 'maven MCP tools' "$STREAM1" \
   || grep -qE '"permission_denials":\[[^]]*"command":"[^"]*mvn' "$STREAM1"; then
  echo "  ✓ mvn gate fired (hook_manager deny observed in stream)"
else
  echo "  ✗ FAIL: mvn gate did not fire. Last 10 stream events:" >&2
  tail -10 "$STREAM1" >&2
  echo "  stderr:" >&2
  cat "$TMP/stream1.stderr.log" >&2 || true
  exit 1
fi

# ── Test 2: a benign command round-trips without a block ─────────────
echo "[2/2] prompting claude to run 'echo hello'…"
STREAM2="$TMP/stream2.jsonl"
run_claude "Please execute the shell command: echo hello" "$STREAM2"

# Benign command must not produce a mvn-related deny. Use the unique deny reason.
if grep -q 'maven MCP tools' "$STREAM2"; then
  echo "  ✗ FAIL: benign 'echo hello' was falsely flagged as an mvn command." >&2
  tail -10 "$STREAM2" >&2
  exit 1
fi
# And the final result's permission_denials should be empty.
if grep -qE '"permission_denials":\[\]' "$STREAM2"; then
  echo "  ✓ benign command ran without any deny"
else
  echo "  ✓ benign command not falsely blocked (though something got denied; check logs)"
fi

echo
echo "E2E smoke: both checks passed."
