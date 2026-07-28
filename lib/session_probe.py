"""Resolve a Bash command's own agent-session id, whatever the harness.

Every session-linked skill step (`goal preflight --session-id`, `memory
recall-for-task --session`, the `regin gate` anti-skip checks, `goal feedback
--trace-id`) needs the id of the session running it. There are three ways to
get it, tried in this order:

1. ``REGIN_SESSION_ID`` — the harness-agnostic override. Any agent CLI, or a
   wrapper script around one, can export it, so a harness regin has no adapter
   for still takes part in the session-linked loops.
2. A provider's own variable — Claude Code (>= ~2.1) exports
   ``CLAUDE_CODE_SESSION_ID`` into every child process's environment. Providers
   declare theirs as `AgentProvider.session_id_env_vars`; harnesses that export
   nothing declare none.
3. `resolve_from_trace()` — opt-in, for the harnesses in that last group: the
   one *recently active* session regin's own hooks recorded for this working
   directory. Ambiguity (two such sessions in one directory) resolves to None
   rather than to a guess, which is what made the earlier cwd-keyed *cache*
   unusable — it could hand back a sibling or parent session's id with no way
   to tell.

For a Claude *child* session (`CLAUDE_CODE_CHILD_SESSION=1`) the env var is the
child's own id, which is where that context's trace spans land (unlike the
background-task output directory, which is named with the PARENT session id).
So skills read the id from here (`regin session-id`), never reconstruct it from
a Task tool's output path.
"""

from __future__ import annotations

import os
from typing import Optional

# Harness-agnostic override, consulted before any provider's native variable.
_PORTABLE_ENV = "REGIN_SESSION_ID"

# Claude Code's native variable. Also declared by `ClaudeProvider`; kept here as
# the fallback candidate list for when the provider registry can't be imported.
_ENV_SESSION_ID = "CLAUDE_CODE_SESSION_ID"


def env_candidates() -> tuple[str, ...]:
    """Env var names to consult, most-authoritative first, deduplicated."""
    names = [_PORTABLE_ENV]
    try:
        from lib.providers.registry import provider_session_id_env_vars
        names.extend(provider_session_id_env_vars())
    except Exception:
        names.append(_ENV_SESSION_ID)
    return tuple(dict.fromkeys(names))


def resolve() -> Optional[str]:
    """Return the current session id from the environment, or None.

    Returns None when no candidate variable is set (a harness that exports
    nothing, or an invocation outside a session), so callers can treat empty as
    "omit the flag". `resolve_from_trace()` is the opt-in second chance.
    """
    for name in env_candidates():
        value = os.environ.get(name)
        if value:
            return value
    return None


#: How recently a session must have been seen to count as "the one running
#: here". `ended_at` alone is not enough: it is written on a clean SessionEnd,
#: so every crashed or killed session leaks a permanently-unended row — on a
#: working checkout most "live" rows are days old. Hooks fire on every tool
#: call, so a genuinely active session is always minutes fresh.
LIVE_WINDOW_MINUTES = 30


def resolve_from_trace(cwd: Optional[str] = None,
                       window_minutes: int = LIVE_WINDOW_MINUTES) -> Optional[str]:
    """The single recently-active session regin recorded for `cwd`, or None.

    For harnesses that export no session id at all: regin's own hooks already
    wrote a `sessions` row under the harness's id, keyed by the starting
    working directory. This reads that back.

    Returns None unless exactly one candidate survives, so two agents running
    in the same checkout degrade to "no id" instead of to each other's id.
    """
    from datetime import datetime, timedelta

    from sqlalchemy import or_
    from sqlmodel import select

    from lib.orm import SessionLocal
    from lib.orm.models.trace import Session as SessionRow

    # `realpath`, not just `abspath`: on macOS a session started in /tmp/x is
    # recorded under /private/tmp/x, so the plain absolute path never matches.
    target = os.path.realpath(cwd or os.getcwd())
    try:
        with SessionLocal() as db:
            rows = db.exec(
                select(SessionRow.trace_id, SessionRow.last_seen)
                .where(SessionRow.cwd == target)
                .where(SessionRow.ended_at.is_(None))
                .where(SessionRow.is_test == 0)
                # A NULL origin reads as 'session' everywhere else in regin, so
                # it must here too or older rows are silently invisible.
                .where(or_(SessionRow.origin == "session",
                           SessionRow.origin.is_(None)))
            ).all()
    except Exception:
        # An absent or un-migrated DB (a fresh install that never ran `regin
        # init`) is a miss, not a traceback: `regin session-id` promises empty
        # stdout + exit 0 on a miss so `SID=$(… session-id)` stays composable.
        return None
    # The recency test happens in Python, and so does the ordering — neither
    # can be a SQL string comparison. `last_seen` has writers using both "T"
    # and space separators, and a space-stamp sorts *below* every same-day
    # "T" stamp, so ordering (or filtering) on the raw text drops a live
    # session behind stale ones. Parsing decides both correctly.
    now = datetime.now()
    window = timedelta(minutes=window_minutes)
    fresh = [tid for tid, seen in rows
             if (age := _age(now, seen)) is not None and age <= window]
    return fresh[0] if len(fresh) == 1 else None


def _age(now, last_seen: Optional[str]):
    """How long ago `last_seen` was, or None when it can't be parsed.

    Absolute, so a stamp from the future (clock skew, or a writer using a
    different timezone) is *out* of the window rather than trivially inside it.
    """
    from datetime import datetime

    if not last_seen:
        return None
    try:
        seen = datetime.fromisoformat(last_seen)
    except (TypeError, ValueError):
        return None
    if seen.tzinfo is not None:
        seen = seen.astimezone().replace(tzinfo=None)
    return abs(now - seen)
