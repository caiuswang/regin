"""Handler: PreToolUse → stamp the live session id into the session cache.

An agent often needs its own Claude Code session id — to stamp
`goal feedback --trace-id`, `goal preflight --session-id`, or the `gate`
anti-skip checks — but Claude Code never exposes the id to Bash. The hook
payload *does* carry `session_id`, so on every Bash call this handler records
it into the cache in `lib/session_probe.py`, keyed by cwd (and by any `--nonce`
token in the command).

The real `regin session-id` CLI command reads that cache back. Because the
stamp lands on the probe command's *own* PreToolUse — which fires immediately
before the command runs — a single `SID=$(.venv/bin/python cli/regin.py
session-id)` resolves to the right id with no prior step, and it works through
`$(…)` substitution and the full interpreter form alike.

No command rewriting: `session-id` is a real, always-present subcommand, so
there is nothing to intercept — the handler only records and never alters the
command or its permission decision.
"""

from __future__ import annotations

from lib import session_probe

from ..core import HookPayload, HookResponse


def handle(payload: HookPayload) -> HookResponse | None:
    if payload.tool_name != 'Bash':
        return None
    sid = payload.session_id
    if not sid:
        return None
    ti = payload.tool_input or {}
    cmd = ti.get('command') if isinstance(ti, dict) else None
    # Stamp the cache so `regin session-id` (the real CLI command) resolves
    # THIS session. Never raises — it is on the hot PreToolUse path.
    session_probe.record(sid, cwd=payload.cwd,
                         command=cmd if isinstance(cmd, str) else None)
    return None
