#!/usr/bin/env bash
# Setup regin on a new machine.
# Usage: ./scripts/setup.sh [scan_path]
#
# What it does:
#   1. Creates Python venv and installs dependencies
#   2. Installs frontend dependencies and builds the SPA
#   3. Initializes SQLite (local cache) from git-tracked files
#   4. Configures local settings (mode, database_url) and registers source repos
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
REPO_ARG="${1:-}"
echo "[4/6] Configuring local settings..."

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
  "mode": "shared",
  "database_url": "$DB_URL"
}
EOF
    else
      cat > config/settings.local.json <<EOF
{
  "mode": "shared"
}
EOF
      echo "  Skipped — configure later in config/settings.local.json"
    fi
  fi
fi
echo "  Local settings saved"

# --- 4b. Agent bridge (optional) ---
# Lets the /live page send prompts / steering messages into a running claude
# session via tmux. Full checklist: docs/setup.md § Agent bridge.
if ! grep -q '"agent_bridge"' config/settings.local.json; then
  echo ""
  read -rp "  Enable the agent bridge (steer live claude sessions from /live)? [y/N]: " BRIDGE_INPUT
  if [[ "${BRIDGE_INPUT:-n}" =~ ^[Yy]$ ]]; then
    .venv/bin/python - <<'PY'
import json, pathlib
p = pathlib.Path('config/settings.local.json')
cfg = json.loads(p.read_text())
cfg['agent_bridge'] = {'enabled': True}
p.write_text(json.dumps(cfg, indent=2) + '\n')
PY
    echo "  agent_bridge.enabled=true written to config/settings.local.json"
    echo "  Also required: export REGIN_BRIDGE=1 in the shell that launches claude,"
    echo "  run claude inside tmux, and log in with an editor-role account."
  fi
fi

# --- 4c. Register source repos ---
# `repo_paths` holds explicit git working trees. Register the given path if it
# is itself a repo, else each immediate child that is one (the documented
# "folder of your source repos"). Without an arg, register nothing — repos are
# added later via `regin add-repo` or the /repos page.
if [ -n "$REPO_ARG" ]; then
  register_repo() {
    if ! .venv/bin/python cli/regin.py add-repo "$1"; then
      echo "  warning: could not register $1"
    fi
  }
  if [ -e "$REPO_ARG/.git" ]; then
    echo "  Registering repo: $REPO_ARG"
    register_repo "$REPO_ARG"
  else
    echo "  Scanning $REPO_ARG for git repos to register..."
    FOUND_REPO=0
    for child in "$REPO_ARG"/*/; do
      child="${child%/}"
      if [ -e "$child/.git" ]; then
        register_repo "$child"
        FOUND_REPO=1
      fi
    done
    [ "$FOUND_REPO" -eq 0 ] && echo "  No git repos found directly under $REPO_ARG"
  fi
  .venv/bin/python cli/regin.py discover
else
  echo "  No source path given — register repos later with:"
  echo "    .venv/bin/python cli/regin.py add-repo /path/to/your/source-repo"
  echo "    (or the /repos page in the web UI)"
fi

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
