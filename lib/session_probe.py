"""Resolve a Bash command's own Claude Code session id.

Newer Claude Code (>= ~2.1) exports the live id as `CLAUDE_CODE_SESSION_ID`,
so `resolve()` reads it directly — the authoritative path. The legacy
machinery below remains as a fallback for older Claude Code that did NOT expose
it: the PreToolUse hook (`hook_manager/handlers/session_id_probe.py`)
`record()`s a `{sid, ts, nonce}` entry on each Bash call — a timestamped stamp
of the current session — and `resolve()` reads the freshest entry (by cwd, or
by an explicit `--nonce`).

The cwd cache is heuristic: any session running a Bash call in the same
directory overwrites that cwd's stamp, so on its own it can return a sibling or
parent session's id. Preferring the env var removes that failure mode.

This replaces the fragile command-rewriting probe with a durable cache:
`session-id` is a real, always-present CLI subcommand, so it never errors with
"No such command", and it works through `$(...)` substitution and the full
`.venv/bin/python cli/regin.py session-id` interpreter form alike. The hook
records the cache on the probe command's *own* PreToolUse, which fires
immediately before the command runs — so a single `SID=$(… session-id)` call
resolves to the right id without any prior step.

Storage is one small JSON file under `settings.data_dir`. Writes are atomic
(temp + `os.replace`); a rare lost update across concurrent hook processes is
benign because each write merely re-stamps the *current* session. Entries older
than `_MAX_AGE_S` are pruned on write, and the per-axis maps are capped.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Optional

from lib.settings import settings

# Stamps older than this are ignored on resolve and dropped on write — a stale
# cwd→session mapping (e.g. a closed session) must never be returned.
_MAX_AGE_S = 24 * 3600
_MAX_CWD = 64
_MAX_NONCE = 128

# Claude Code (>= ~2.1) exports the live session id to every child process's
# environment. This is the authoritative source — `resolve()` prefers it over
# the cwd cache, which any session sharing the working directory clobbers on
# its next Bash call. For a *child* session (`CLAUDE_CODE_CHILD_SESSION=1`)
# this is the child's own id, which is where that context's trace spans land —
# unlike the background-task output directory, which is named with the parent
# session id. So skills must read the id from here (`regin session-id`), never
# reconstruct it from a Task tool's output path.
_ENV_SESSION_ID = "CLAUDE_CODE_SESSION_ID"

# Pulls an explicit correlation token out of the probe command, e.g.
# `regin session-id --nonce 1a2b…`. Lets the agent disambiguate concurrent
# sessions sharing a cwd when the freshest-wins default is not enough.
_NONCE_RE = re.compile(r'--(?:nonce|session-nonce)[= ]+(\S+)')


def _cache_path() -> Path:
    return Path(settings.data_dir) / "session_probe.json"


def _load() -> dict:
    try:
        with open(_cache_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    return {"by_cwd": {}, "by_nonce": {}}


def _atomic_write(data: dict) -> None:
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".session_probe.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _prune(bucket: dict, now: float, cap: int) -> dict:
    fresh = {k: v for k, v in bucket.items()
             if isinstance(v, dict) and now - v.get("ts", 0) <= _MAX_AGE_S}
    if len(fresh) <= cap:
        return fresh
    newest = sorted(fresh.items(), key=lambda kv: kv[1].get("ts", 0), reverse=True)
    return dict(newest[:cap])


def record(session_id: Optional[str], cwd: Optional[str] = None,
           command: Optional[str] = None, *, ts: Optional[float] = None) -> None:
    """Stamp `session_id` as current for `cwd` (and any `--nonce` in `command`).

    Called from the hot PreToolUse path, so it must never raise — all I/O is
    wrapped and failures are swallowed.
    """
    if not session_id:
        return
    now = ts if ts is not None else time.time()
    try:
        data = _load()
        by_cwd = data.get("by_cwd") if isinstance(data.get("by_cwd"), dict) else {}
        by_nonce = data.get("by_nonce") if isinstance(data.get("by_nonce"), dict) else {}
        stamp = {"sid": session_id, "ts": now}
        if cwd:
            by_cwd[cwd] = stamp
        if command:
            m = _NONCE_RE.search(command)
            if m:
                by_nonce[m.group(1)] = stamp
        data["by_cwd"] = _prune(by_cwd, now, _MAX_CWD)
        data["by_nonce"] = _prune(by_nonce, now, _MAX_NONCE)
        _atomic_write(data)
    except (OSError, ValueError, TypeError):
        pass


def _bucket(data: dict, key: str) -> dict:
    val = data.get(key)
    return val if isinstance(val, dict) else {}


def resolve(cwd: Optional[str] = None, nonce: Optional[str] = None) -> Optional[str]:
    """Return the live session id: explicit `nonce`, else the env var, else
    `cwd`, else the sole cached session.

    The env var (`CLAUDE_CODE_SESSION_ID`) is authoritative and takes priority
    over the cwd cache — the cache is clobbered whenever another session runs a
    Bash call in the same directory, which is why `regin session-id` could
    return a sibling/parent session's id. An explicit `nonce` still wins over
    the env, so a caller can pin a specific concurrent run. Stale entries
    (> `_MAX_AGE_S`) are never returned; None on a total miss.
    """
    now = time.time()
    data = _load()
    by_cwd = _bucket(data, "by_cwd")
    by_nonce = _bucket(data, "by_nonce")

    def _fresh(entry) -> Optional[str]:
        if isinstance(entry, dict) and now - entry.get("ts", 0) <= _MAX_AGE_S:
            return entry.get("sid")
        return None

    # An explicit nonce is a deliberate correlation token — honor it first,
    # even over the ambient env (lets a caller pin one of several runs).
    if nonce:
        sid = _fresh(by_nonce.get(nonce))
        if sid:
            return sid

    # Authoritative for the current process; survives the cwd-clobber failure
    # mode entirely. Only the cache below is consulted on older Claude Code
    # that doesn't export the id.
    env_sid = os.environ.get(_ENV_SESSION_ID)
    if env_sid:
        return env_sid

    sid = _fresh(by_cwd.get(cwd))
    if sid:
        return sid
    # Fallback ONLY when unambiguous: if every fresh stamp belongs to the same
    # session, return it (covers a cwd that drifted via symlink/trailing slash
    # in a single-session repo). With >1 distinct session live (concurrent
    # agents), a cwd miss returns None rather than risk mis-attributing the run
    # to an unrelated session.
    fresh = {s for v in by_cwd.values() if (s := _fresh(v))}
    return next(iter(fresh)) if len(fresh) == 1 else None
