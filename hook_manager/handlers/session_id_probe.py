"""Handler: PreToolUse → answer a session-id probe with the live session id.

An agent often needs its own Claude Code session id — to stamp
`goal feedback --trace-id`, `goal preflight --session-id`, or anything else
that should link back to the run — but Claude Code never exposes the id to
Bash. The hook payload *does* carry `session_id`, so this handler lets the
agent simply *ask* for it: run the sentinel command and the hook rewrites it
(via `updated_input`) to echo the id, which the agent then reads off stdout
and concatenates however it likes.

    SID=$(regin session-id)
    .venv/bin/python cli/regin.py goal feedback "$goal" --trace-id "$SID" …

Keeping the probe a standalone command (not a command-substitution embedded in
a larger line) keeps the match strict and the rewrite predictable: the hook
only ever replaces a bare probe, never mutates a real command. The agent owns
the composition step.
"""

from __future__ import annotations

import re
import shlex

from ..core import HookPayload, HookResponse

# The whole Bash command must be just the probe — `regin session-id`,
# `regin-session-id`, `regin session id`, etc. Nothing else is touched.
_PROBE_RE = re.compile(r'^\s*regin[ -]session[ -]id\s*$', re.IGNORECASE)


def handle(payload: HookPayload) -> HookResponse | None:
    if payload.tool_name != 'Bash':
        return None
    sid = payload.session_id
    if not sid:
        return None
    ti = payload.tool_input or {}
    cmd = ti.get('command') if isinstance(ti, dict) else None
    if not isinstance(cmd, str) or not _PROBE_RE.match(cmd):
        return None
    # Rewrite to a bare echo of the id, so the agent gets exactly the session
    # id on stdout (newline-terminated; `$(…)` capture strips it).
    return HookResponse(
        permission_decision='allow',
        permission_reason='session-id probe answered by hook',
        updated_input={**ti, 'command': f"printf '%s\\n' {shlex.quote(sid)}"},
    )
