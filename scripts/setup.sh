#!/usr/bin/env bash
# Install regin, or upgrade an existing install — the same command does both.
# Usage: ./scripts/setup.sh [scan_path]
#        PYTHON=/path/to/python3.12 ./scripts/setup.sh   # pick the interpreter
#
# Re-running it is safe: the database, local settings and users are kept;
# dependencies, the web UI and the schema are brought up to date.
#
# Without a terminal on stdin every question takes its default answer
# (standalone mode, no agent bridge, wire the Claude hooks, no user created).

set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$(pwd)

PYTHON="${PYTHON:-python3}"
MIN_PY="3.11"

if [ -t 0 ]; then INTERACTIVE=1; else INTERACTIVE=0; fi

# ask <prompt> <default> — echoes the answer, or the default when non-interactive.
ask() {
  local reply=""
  if [ "$INTERACTIVE" -eq 1 ]; then
    read -rp "$1" reply || true
  fi
  echo "${reply:-$2}"
}

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

echo "=== regin setup ==="
echo ""

# --- Preflight: interpreter and toolchain, before anything is written ---
py_version() {
  "$1" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null
}

py_at_least_min() {
  local major minor
  IFS=. read -r major minor <<<"$1"
  [ "$major" -gt 3 ] || { [ "$major" -eq 3 ] && [ "$minor" -ge 11 ]; }
}

if [ -d .venv ]; then
  # A venv whose interpreter vanished (e.g. after a Homebrew Python upgrade)
  # can't be repaired in place.
  [ -x .venv/bin/python ] && .venv/bin/python -c '' 2>/dev/null \
    || fail "The existing .venv has no working interpreter. Remove .venv and re-run with PYTHON=/path/to/python$MIN_PY (or newer)."
  PY_CHECKED=.venv/bin/python
else
  command -v "$PYTHON" >/dev/null 2>&1 || fail "Python interpreter '$PYTHON' not found. regin needs Python >= $MIN_PY; set PYTHON=/path/to/python3.11 (or newer)."
  PY_CHECKED="$PYTHON"
fi
PY_VER=$(py_version "$PY_CHECKED") || fail "Could not run '$PY_CHECKED' to read its version."
if ! py_at_least_min "$PY_VER"; then
  if [ "$PY_CHECKED" = ".venv/bin/python" ]; then
    fail "The existing .venv runs Python $PY_VER; regin needs Python >= $MIN_PY. Remove .venv and re-run with PYTHON=/path/to/python$MIN_PY (or newer)."
  fi
  fail "Python $PY_VER at '$PY_CHECKED' is too old; regin needs Python >= $MIN_PY. Re-run with PYTHON=/path/to/python$MIN_PY (or newer)."
fi
echo "[check] Python $PY_VER"

# vite 8 declares engines.node "^20.19.0 || >=22.12.0".
node_supported() {
  local major minor
  IFS=. read -r major minor _ <<<"${1#v}"
  if [ "$major" -eq 20 ]; then [ "$minor" -ge 19 ]; return; fi
  if [ "$major" -eq 22 ]; then [ "$minor" -ge 12 ]; return; fi
  [ "$major" -gt 22 ]
}

command -v node >/dev/null 2>&1 || fail "Node.js not found. The web UI build (vite 8) needs Node ^20.19 or >= 22.12."
command -v npm >/dev/null 2>&1 || fail "npm not found. Install npm alongside Node.js (^20.19 or >= 22.12)."
NODE_VER=$(node --version)
node_supported "$NODE_VER" || fail "Node $NODE_VER is not supported by vite 8; install Node ^20.19 or >= 22.12."
echo "[check] Node $NODE_VER, npm $(npm --version)"

# --- 0. Git hooks (only inside a git repo; never switches branch) ---
if git rev-parse --git-dir > /dev/null 2>&1; then
  echo "[0] Installing git hooks from .githooks/..."
  git config core.hooksPath .githooks
else
  echo "[0] Not a git repository — skipping git hook install"
fi

# --- 1. Python environment ---
if [ ! -d .venv ]; then
  echo "[1/7] Creating Python virtual environment..."
  "$PYTHON" -m venv .venv
else
  echo "[1/7] Python venv already exists"
fi
echo "  Installing Python dependencies (this may take a few minutes)..."
.venv/bin/python -m pip install -e '.[agent-sdk]'
echo "  Dependencies installed"

# --- 1b. CLI on PATH ---
# `.venv/bin/regin` is only reachable while the venv is active, but agents run
# `regin ...` from other repos' sessions: hooks keep working (absolute
# interpreter path) while every skill's CLI leg fails silently.
BIN_DIR="${REGIN_BIN_DIR:-$HOME/.local/bin}"
mkdir -p "$BIN_DIR"
ln -sf "$ROOT/.venv/bin/regin" "$BIN_DIR/regin"
echo "[1b] Linked regin CLI -> $BIN_DIR/regin"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo "  WARNING: $BIN_DIR is not on PATH. Add it, or agents in other repos" ;
     echo "           cannot run 'regin' and every skill's CLI gate silently fails." ;;
esac

# --- 2. Frontend build ---
echo "[2/7] Building frontend..."
(cd frontend && npm ci && npx vite build)
echo "  Frontend built"

# --- 3. Database ---
echo "[3/7] Preparing the database..."
.venv/bin/python cli/regin.py init
.venv/bin/python cli/regin.py migrate
echo "  Database at the current schema"

# --- 4. Local settings ---
REPO_ARG="${1:-}"
echo "[4/7] Configuring local settings..."

if [ -f config/settings.local.json ]; then
  echo "  config/settings.local.json already exists — keeping it"
else
  echo ""
  echo "  Choose server mode:"
  echo "    shared   = team mode with MySQL for user accounts/audit"
  echo "    standalone = single-user mode with local SQLite only"
  MODE=$(ask "  mode [standalone]: " standalone)

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
    DB_URL=$(ask "  database_url (or Enter to skip): " "")

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
  BRIDGE_INPUT=$(ask "  Enable the agent bridge (steer live claude sessions from /live)? [y/N]: " n)
  if [[ "$BRIDGE_INPUT" =~ ^[Yy]$ ]]; then
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
# is itself a repo, else each immediate child that is one. Without an arg,
# register nothing — repos are added later via `regin add-repo` or /repos.
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
  echo "    regin add-repo /path/to/your/source-repo"
  echo "    (or the /repos page in the web UI)"
fi

# --- 5. Claude Code hook wiring ---
echo "[5/7] Claude Code hook wiring..."
CLAUDE_WIRED=$(.venv/bin/python cli/regin.py hooks status --provider claude --json 2>/dev/null \
  | .venv/bin/python -c 'import json, sys; rows = json.load(sys.stdin); print(int(any(r["hook"] == "hook_manager" and r["installed"] for r in rows)))' \
  2>/dev/null || echo 0)
if [ "$CLAUDE_WIRED" = "1" ]; then
  echo "  Already wired — repairing any drifted command"
  .venv/bin/python cli/regin.py hooks repair --provider claude \
    || echo "  warning: hook repair reported a problem — see the output above"
else
  HOOKS_INPUT=$(ask "  Wire regin's hooks into Claude Code (~/.claude/settings.json)? [Y/n]: " y)
  if [[ "$HOOKS_INPUT" =~ ^[Yy] ]]; then
    .venv/bin/python cli/regin.py hooks install --provider claude \
      || echo "  warning: hook install failed — retry with: regin hooks install --provider claude"
  else
    echo "  Skipped — wire later with: regin hooks install --provider claude"
  fi
fi

# --- 6. Auth/audit tables ---
# Standalone keeps users/audit_log in the main SQLite DB, which `init` built.
echo "[6/7] Auth/audit tables..."
MODE_CHECK=$(.venv/bin/python -c "from lib.settings import settings; print(settings.mode)" 2>/dev/null || echo "shared")
MYSQL_READY=0
if [ "$MODE_CHECK" = "standalone" ]; then
  echo "  Standalone mode — users live in the local SQLite database"
elif .venv/bin/python -c "from lib.mysql_db import is_configured; exit(0 if is_configured() else 1)" 2>/dev/null; then
  .venv/bin/python cli/regin.py users init-db
  MYSQL_READY=1
  echo "  MySQL tables ready"
else
  echo "  MySQL not configured — skipping. Add database_url to config/settings.local.json"
fi

# --- 7. First user ---
echo ""
echo "[7/7] User account"
if [ "$MODE_CHECK" = "standalone" ] || [ "$MYSQL_READY" -eq 1 ]; then
  EXISTING=$(.venv/bin/python -c "from lib.auth import user_count; print(user_count())")
  if [ "$EXISTING" -gt 0 ]; then
    echo "  $EXISTING user(s) already exist — not creating another"
  elif [ "$INTERACTIVE" -eq 0 ]; then
    echo "  No users yet. Create the admin with:"
    echo "    regin users create <username> <password> --role admin"
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
echo "  regin serve"
echo "Then open http://localhost:$WEB_PORT"
echo ""
echo "Upgrade later (keeps your database, settings and users):"
echo "  git pull && ./scripts/setup.sh"
echo ""
echo "Uninstall (removes hook wiring, deployed skills and the regin link; add --purge-data to drop data):"
echo "  regin uninstall"
