#!/usr/bin/env bash
# regin MCP server launcher for the regin-agents plugin.
#
# This is the ONE place in the plugin that knows where the regin checkout lives.
# Everything else is path-portable: .mcp.json references THIS script via
# ${CLAUDE_PLUGIN_ROOT}/bin/regin-mcp.sh, so the plugin can be installed to the
# versioned plugin cache without any absolute path baked into its config.
#
# It resolves the regin checkout from $REGIN_HOME (default: ~/regin) and execs
# that checkout's venv python on the requested server module. This is the
# external-CLI boundary made explicit — see README.md. A future version replaces
# this with a pip-installed `regin-mcp-*` console entrypoint so no checkout path
# is needed at all.
#
# Usage: regin-mcp.sh <server-module-path-relative-to-regin-root>
set -euo pipefail

REGIN_HOME="${REGIN_HOME:-$HOME/regin}"
SERVER_REL="${1:?usage: regin-mcp.sh <server-module-path relative to regin root>}"

PY="$REGIN_HOME/.venv/bin/python"
SERVER="$REGIN_HOME/$SERVER_REL"

if [ ! -x "$PY" ]; then
  echo "regin-mcp.sh: no venv python at $PY (set REGIN_HOME to your regin checkout)" >&2
  exit 1
fi
if [ ! -f "$SERVER" ]; then
  echo "regin-mcp.sh: no server module at $SERVER" >&2
  exit 1
fi

exec "$PY" "$SERVER"
