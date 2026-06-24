"""Memory-cache that lets a Bash command recover its own Claude Code session id.

Claude Code never exposes the live session id to Bash, but every hook payload
carries it. So the PreToolUse hook (`hook_manager/handlers/session_id_probe.py`)
`record()`s a `{sid, ts, nonce}` entry here on each Bash call — a timestamped
stamp of the current session — and the real `regin session-id` CLI command
`resolve()`s the freshest entry (by cwd, or by an explicit `--nonce`) and prints
it.

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
    """Return the live session id for `nonce`, else `cwd`, else the sole session.

    Exact `nonce`/`cwd` matches win. On a miss, only fall back when the cache
    holds exactly one distinct live session (never guess among concurrent
    sessions). Stale entries (> `_MAX_AGE_S`) are never returned; None on a miss.
    """
    now = time.time()
    data = _load()
    by_cwd = _bucket(data, "by_cwd")
    by_nonce = _bucket(data, "by_nonce")

    def _fresh(entry) -> Optional[str]:
        if isinstance(entry, dict) and now - entry.get("ts", 0) <= _MAX_AGE_S:
            return entry.get("sid")
        return None

    # Exact matches first: explicit nonce, then this cwd.
    for entry in (by_nonce.get(nonce), by_cwd.get(cwd)):
        sid = _fresh(entry)
        if sid:
            return sid
    # Fallback ONLY when unambiguous: if every fresh stamp belongs to the same
    # session, return it (covers a cwd that drifted via symlink/trailing slash
    # in a single-session repo). With >1 distinct session live (concurrent
    # agents), a cwd miss returns None rather than risk mis-attributing the run
    # to an unrelated session.
    fresh = {s for v in by_cwd.values() if (s := _fresh(v))}
    return next(iter(fresh)) if len(fresh) == 1 else None
