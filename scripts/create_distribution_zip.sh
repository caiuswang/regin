#!/usr/bin/env bash
# Create a distribution zip of regin with sensitive files removed.
# Usage: ./scripts/create_distribution_zip.sh [output_path]
#
# Excludes:
#   - .git/ (contains author emails/names in history)
#   - .claude/, .regin/ (local settings, session data, runtime state)
#   - db/*.db* and root regin.db (SQLite DB + WAL/SHM with user accounts and audit logs)
#   - config/settings.local.json (machine-specific overrides)
#   - config/jwt_secret.txt (JWT signing secret)
#   - .venv/, node_modules/ (rebuildable dependencies)
#   - screenshots, test artifacts, coverage, build caches

set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$(pwd)

OUTPUT="${1:-$ROOT/regin.zip}"
TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

echo "=== Creating distribution zip ==="
echo "  Source: $ROOT"
echo "  Output: $OUTPUT"
echo ""

# Copy project to temp dir, excluding sensitive/rebuildable files
rsync -a \
  --exclude='.git' \
  --exclude='.claude' \
  --exclude='.regin' \
  --exclude='regin.zip' \
  --exclude='db/*.db*' \
  --exclude='regin.db' \
  --exclude='config/settings.local.json' \
  --exclude='config/jwt_secret.txt' \
  --exclude='.venv' \
  --exclude='node_modules' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='.DS_Store' \
  --exclude='.coverage' \
  --exclude='.pytest_cache' \
  --exclude='dist' \
  --exclude='build' \
  --exclude='*.egg-info' \
  --exclude='web/static/dist' \
  --exclude='session-trace-*.png' \
  --exclude='settings-page-*.png' \
  --exclude='sessions-duration-*.png' \
  --exclude='.playwright-mcp' \
  --exclude='frontend/screenshots' \
  --exclude='frontend/test-results' \
  "$ROOT/" "$TMPDIR/regin/"

# Create zip
cd "$TMPDIR"
zip -r -q "$(basename "$OUTPUT")" regin/
mv "$(basename "$OUTPUT")" "$OUTPUT"

echo "Done."
echo "  Size: $(du -h "$OUTPUT" | cut -f1)"
echo "  Path: $OUTPUT"
