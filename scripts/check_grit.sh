#!/bin/bash
# check_grit.sh — Run GritQL pattern checks against a repo
#
# Usage:
#   ./scripts/check_grit.sh <repo-path> [pattern-name]
#
# Trigger matching is delegated to the regin web API.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# Honor GRIT_DIR from the environment so callers (regin CLI, skill bundles
# on newer deploys) can point the script at a user-local grit directory.
# Fall back to the sibling `.grit/` relative to this script's location —
# matches how the deployed grit-rules skill is laid out.
GRIT_DIR="${GRIT_DIR:-$(dirname "$SCRIPT_DIR")/.grit}"
RULES_JSON="$GRIT_DIR/rules.json"
REPO_PATH="${1:?Usage: check_grit.sh <repo-path> [pattern-name]}"
PATTERN_NAME="${2:-}"
API_BASE="${REGIN_CODEBASE_API:-http://127.0.0.1:8321}"

if [ ! -d "$REPO_PATH" ]; then echo "Error: repo not found: $REPO_PATH" >&2; exit 1; fi
REPO_PATH="$(cd "$REPO_PATH" && pwd)"
command -v jq >/dev/null 2>&1 || { echo "Error: jq required" >&2; exit 2; }

# Copy .grit once, clean up on exit
NEEDS_CLEANUP=false
if [ ! -d "$REPO_PATH/.grit" ]; then
    cp -r "$GRIT_DIR" "$REPO_PATH/.grit"
    NEEDS_CLEANUP=true
fi
trap '[ "$NEEDS_CLEANUP" = true ] && rm -rf "$REPO_PATH/.grit"' EXIT

VIOLATIONS=0
CHECKED=0
OUT=$(mktemp)

run_grit() {
    CHECKED=$((CHECKED + 1))
    echo -n "--- Checking: $1 --- "
    cd "$REPO_PATH" && grit apply "$1" --dry-run > "$OUT" 2>&1 < /dev/null || true
    # Filter output: remove violations from files that don't match triggers
    python3 "$SCRIPT_DIR/filter_grit_output.py" "$RULES_JSON" "$1" "$REPO_PATH" < "$OUT" > "${OUT}.filtered"
    mv "${OUT}.filtered" "$OUT"
    local m=$(grep -oE '[0-9]+ match' "$OUT" | grep -oE '[0-9]+')
    if [ -n "$m" ] && [ "$m" != "0" ]; then
        printf "VIOLATION: %s match(es)\n" "$m"
        head -20 "$OUT"
        VIOLATIONS=$((VIOLATIONS + 1))
    else
        printf "OK\n"
    fi
}

echo "========================================="
echo "Pattern Checker — $REPO_PATH"
echo "========================================="
echo ""

if [ -n "$PATTERN_NAME" ]; then
    run_grit "$PATTERN_NAME"
else
    encoded=$(python3 -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1]))" "$REPO_PATH" 2>/dev/null || echo "$REPO_PATH")
    IDS=$(mktemp)
    curl -sf "${API_BASE}/api/applicable-rules?repo=${encoded}" 2>/dev/null | jq -r '.[].id' > "$IDS" || true
    total=$(jq '[.rules[]|select(.disabled!=true)]|length' "$RULES_JSON")
    applicable=$(wc -l < "$IDS" | tr -d ' ')
    echo "Found $applicable applicable rules (of $total total)"
    echo ""
    while read -r rid; do
        run_grit "$rid"
        echo ""
    done < "$IDS"
    rm -f "$IDS"
fi

rm -f "$OUT"
echo "========================================="
echo "Results: $CHECKED checked, $VIOLATIONS violation(s)"
echo "========================================="
exit $VIOLATIONS
