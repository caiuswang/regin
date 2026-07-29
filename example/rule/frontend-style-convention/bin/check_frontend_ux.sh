#!/bin/bash
# check_frontend_ux.sh — Run frontend-ux checks against a target file
#
# Usage:
#   ./bin/check_frontend_ux.sh <repo-root> <target-file> <rule-json>
#
# A relative <target-file> is resolved against <repo-root>, not the cwd.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RULES_ROOT="${FRONTEND_UX_RULES_ROOT:-$(dirname "$SCRIPT_DIR")}"
RUNNER="$SCRIPT_DIR/frontend-ux-runner.mjs"
REPO_ROOT="${1:?Usage: check_frontend_ux.sh <repo-root> <target-file> <rule-json>}"
TARGET_FILE="${2:?Usage: check_frontend_ux.sh <repo-root> <target-file> <rule-json>}"
RULE_JSON="${3:?Usage: check_frontend_ux.sh <repo-root> <target-file> <rule-json>}"

if [ ! -f "$RUNNER" ]; then echo "Error: runner not found: $RUNNER" >&2; exit 2; fi
if [ ! -d "$REPO_ROOT" ]; then echo "Error: repo not found: $REPO_ROOT" >&2; exit 2; fi
# The runner only joins a relative target onto an absolute root, so `.` and
# other relative roots have to be absolutized here or they are refused.
REPO_ROOT="$(cd "$REPO_ROOT" && pwd)"
case "$TARGET_FILE" in
  /*) RESOLVED_TARGET="$TARGET_FILE" ;;
  *)  RESOLVED_TARGET="$REPO_ROOT/$TARGET_FILE" ;;
esac
if [ ! -f "$RESOLVED_TARGET" ]; then echo "Error: target file not found: $RESOLVED_TARGET" >&2; exit 2; fi
if [ ! -f "$RULE_JSON" ]; then echo "Error: rule json not found: $RULE_JSON" >&2; exit 2; fi

RULE_BODY="$(cat "$RULE_JSON")"
printf '{"repo_root":%s,"file_path":%s,"rule":%s}\n' \
  "$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$REPO_ROOT")" \
  "$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$TARGET_FILE")" \
  "$RULE_BODY" | node "$RUNNER"
