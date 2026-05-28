#!/bin/bash
# check_frontend_ux.sh — Run frontend-ux checks against a target file
#
# Usage:
#   ./bin/check_frontend_ux.sh <repo-root> <target-file> <rule-json>

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RULES_ROOT="${FRONTEND_UX_RULES_ROOT:-$(dirname "$SCRIPT_DIR")}"
RUNNER="$SCRIPT_DIR/frontend-ux-runner.mjs"
REPO_ROOT="${1:?Usage: check_frontend_ux.sh <repo-root> <target-file> <rule-json>}"
TARGET_FILE="${2:?Usage: check_frontend_ux.sh <repo-root> <target-file> <rule-json>}"
RULE_JSON="${3:?Usage: check_frontend_ux.sh <repo-root> <target-file> <rule-json>}"

if [ ! -f "$RUNNER" ]; then echo "Error: runner not found: $RUNNER" >&2; exit 2; fi
if [ ! -d "$REPO_ROOT" ]; then echo "Error: repo not found: $REPO_ROOT" >&2; exit 2; fi
if [ ! -f "$TARGET_FILE" ]; then echo "Error: target file not found: $TARGET_FILE" >&2; exit 2; fi
if [ ! -f "$RULE_JSON" ]; then echo "Error: rule json not found: $RULE_JSON" >&2; exit 2; fi

RULE_BODY="$(cat "$RULE_JSON")"
printf '{"repo_root":%s,"file_path":%s,"rule":%s}\n' \
  "$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$REPO_ROOT")" \
  "$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$TARGET_FILE")" \
  "$RULE_BODY" | node "$RUNNER"
