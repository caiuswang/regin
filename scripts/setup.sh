#!/usr/bin/env bash
# Setup regin on a new machine.
# Usage: ./scripts/setup.sh [scan_path]
#
# What it does:
#   1. Creates Python venv and installs dependencies
#   2. Installs frontend dependencies and builds the SPA
#   3. Initializes SQLite (local cache) from git-tracked files
#   4. Configures local settings (scan_paths, mode, database_url)
#   5. Initializes auth/audit tables and creates first user if needed
#
# After setup, start with:  .venv/bin/python cli/regin.py serve

set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$(pwd)

echo "=== regin setup ==="
echo ""

# --- 0. Install git hooks (only if inside a git repo) ---
# Install on whatever branch the user already checked out — no branch switching.
if git rev-parse --git-dir > /dev/null 2>&1; then
  echo "[0] Installing git hooks from .githooks/..."
  git config core.hooksPath .githooks
else
  echo "[0] Not a git repository — skipping hook install"
fi

# --- 1. Python environment ---
if [ ! -d .venv ]; then
  echo "[1/6] Creating Python virtual environment..."
  python3 -m venv .venv
else
  echo "[1/6] Python venv already exists"
fi
echo "  Installing Python dependencies (this may take a few minutes)..."
# Dependencies are declared in pyproject.toml; `-e .` installs the package + all of them.
.venv/bin/pip install -e .
echo "  Dependencies installed"

# --- 2. Frontend build ---
echo "[2/6] Building frontend..."
if [ ! -d frontend/node_modules ]; then
  echo "  Installing frontend dependencies (this may take a few minutes)..."
  (cd frontend && npm install)
fi
echo "  Building the web UI..."
(cd frontend && npx vite build)
echo "  Frontend built"

# --- 3. SQLite init + rebuild ---
echo "[3/6] Initializing local database cache..."
.venv/bin/python cli/regin.py init --force
.venv/bin/python cli/regin.py rebuild
echo "  SQLite ready"

# --- 4. Local settings ---
SCAN_PATH="${1:-}"
if [ -z "$SCAN_PATH" ]; then
  SCAN_PATH="$(dirname "$ROOT")"
  echo "[4/6] Using default scan path: $SCAN_PATH"
  echo "  Override with: ./scripts/setup.sh /path/to/your/source-repos"
else
  echo "[4/6] Using scan path: $SCAN_PATH"
fi

# Check if settings.local.json already exists
if [ -f config/settings.local.json ]; then
  echo "  config/settings.local.json already exists — keeping it"
else
  echo ""
  echo "  Choose server mode:"
  echo "    shared   = team mode with MySQL for user accounts/audit"
  echo "    standalone = single-user mode with local SQLite only"
  read -rp "  mode [standalone]: " MODE_INPUT
  MODE="${MODE_INPUT:-standalone}"

  if [ "$MODE" = "standalone" ]; then
    cat > config/settings.local.json <<EOF
{
  "scan_paths": ["$SCAN_PATH"],
  "mode": "standalone"
}
EOF
    echo "  Standalone mode selected — no MySQL needed."
  else
    echo "  MySQL connection for shared user accounts."
    echo "  Format: mysql://user:password@host:3306/dbname"
    read -rp "  database_url (or Enter to skip): " DB_URL

    if [ -n "$DB_URL" ]; then
      cat > config/settings.local.json <<EOF
{
  "scan_paths": ["$SCAN_PATH"],
  "mode": "shared",
  "database_url": "$DB_URL"
}
EOF
    else
      cat > config/settings.local.json <<EOF
{
  "scan_paths": ["$SCAN_PATH"],
  "mode": "shared"
}
EOF
      echo "  Skipped — configure later in config/settings.local.json"
    fi
  fi
fi
echo "  Local settings saved"

# Discover repos
.venv/bin/python cli/regin.py discover

# --- 5. Auth/audit tables ---
echo "[5/6] Initializing auth/audit tables..."
MODE_CHECK=$(.venv/bin/python -c "from lib.settings import settings; print(settings.mode)" 2>/dev/null || echo "shared")
if [ "$MODE_CHECK" = "standalone" ]; then
  .venv/bin/python cli/regin.py users init-db
  echo "  SQLite auth/audit tables ready"
else
  if .venv/bin/python -c "from lib.mysql_db import is_configured; exit(0 if is_configured() else 1)" 2>/dev/null; then
    .venv/bin/python cli/regin.py users init-db
    echo "  MySQL tables ready"
  else
    echo "  MySQL not configured — skipping. Add database_url to config/settings.local.json"
  fi
fi

# --- 6. Create first user ---
echo ""
echo "[6/6] Create your user account"
NEED_USER=0
if [ "$MODE_CHECK" = "standalone" ]; then
  NEED_USER=1
else
  if .venv/bin/python -c "from lib.mysql_db import is_configured; exit(0 if is_configured() else 1)" 2>/dev/null; then
    NEED_USER=1
  fi
fi

if [ "$NEED_USER" -eq 1 ]; then
  EXISTING=$(.venv/bin/python -c "from lib.auth import user_count; print(user_count())")
  if [ "$EXISTING" -gt 0 ]; then
    echo "  Users already exist:"
    .venv/bin/python cli/regin.py users list
  else
    echo "  First user will be admin."
    read -rp "  Username: " USERNAME
    read -rp "  Display name: " DISPLAY_NAME
    read -rsp "  Password: " PASSWORD
    echo ""
    .venv/bin/python cli/regin.py users create "$USERNAME" "$PASSWORD" \
      --display-name "${DISPLAY_NAME:-$USERNAME}" --role admin
  fi
else
  echo "  Skipped — MySQL not configured yet."
fi

WEB_PORT=$(.venv/bin/python -c "from lib.settings import settings; print(settings.web_port)" 2>/dev/null || echo 8321)

echo ""
echo "=== Setup complete ==="
echo ""
echo "Start the dashboard:"
echo "  .venv/bin/python cli/regin.py serve"
echo ""
echo "Then open http://localhost:$WEB_PORT"
