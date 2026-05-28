"""Shared ingest-time validators + size/dedup caps.

These live alongside `web/app.py` so every blueprint that handles ingest
(rule-triggers, skill-reads, session-spans) can share one canonical
implementation instead of each re-inventing the guard. Pulling them out
of `web/app.py`'s closure scope also makes them trivially unit-testable
— no Flask app construction required.

Tests historically reference these as `app_module._foo`; `web/app.py`
re-exports them so that surface stays intact.
"""

from __future__ import annotations

import os


# ── Test-session marker normalisation ─────────────────────────

# Canonical home is `lib/trace/is_test.py` so lib-side trace queries can
# share the same SQL fragments without lib → web. Re-exported here so
# `app_module._IS_TEST_CASE` etc. still resolve.
from lib.trace.is_test import (  # noqa: F401
    _IS_TEST_TRUTHY, _IS_TEST_WHERE, _IS_TEST_CASE, _normalize_is_test,
)


# ── Per-field ingest guards ───────────────────────────────────

def _is_iso_timestamp(value) -> bool:
    """True iff `value` is a non-empty string that `datetime.fromisoformat`
    can parse. Catches producer bugs at ingest instead of later crashing
    `/api/sessions/<id>/materialize` with a 500 deep in the projection."""
    from datetime import datetime as _dt
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        _dt.fromisoformat(value)
        return True
    except ValueError:
        return False


def _is_non_blank_str(value) -> bool:
    """True iff `value` is a non-empty, non-whitespace-only string.

    The previous ingest guard used `if not span.get(required)`, which
    *did* reject None and empty strings but let whitespace-only strings
    through — so a producer bug that sent `trace_id='   '` wrote a row
    that never correlates with anything."""
    return isinstance(value, str) and bool(value.strip())


# ── Dedup + batch-cap configuration (env-overridable) ─────────

# Time window for treating an identical ingest POST as a retry of the
# previous one, instead of a new event. Tuned to the hook-plugin retry
# budget (3 attempts × 500 ms + 100 + 200 ms ≈ 2 s). Tests can monkey
# patch this to shrink the window.
_INGEST_DEDUP_WINDOW_SEC = 2.0


# Cap on spans accepted in a single POST /api/session-spans. A runaway
# producer or an accidental infinite-loop hook could otherwise:
#   • exceed SQLite's SQLITE_MAX_VARIABLE_NUMBER (typically 32 766) in
#     the pre-count query — 2 params/span means 1 000 spans = 2 000 <<
#     limit, so 1 000 is a comfortable ceiling;
#   • hold tens of MB in memory while validating the batch;
#   • block Flask for seconds.
# Override via REGIN_INGEST_MAX_BATCH for load-testing. Tests can also
# monkey-patch this constant directly.
_INGEST_MAX_BATCH_SIZE = 1000


def _ingest_max_batch_size() -> int:
    """Resolve the batch cap from env at call time so tests can override."""
    raw = os.environ.get('REGIN_INGEST_MAX_BATCH')
    if raw:
        try:
            v = int(raw)
            if v > 0:
                return v
        except ValueError:
            pass
    return _INGEST_MAX_BATCH_SIZE


# Backstop cap on per-span serialized attributes. The producer handler
# already enforces sane per-attribute caps (command/diff up to 256 KB,
# stdout 64 KB, stderr 32 KB — see hook_manager/handlers/post_tool_trace.py),
# so this is a sanity check against unknown future attribute keys, not
# the primary defense. Worst-case legitimate Bash span ≈ 352 KB
# (command + stdout + stderr); 1 MB gives healthy headroom while still
# rejecting accidents like a hook that stashes a whole file.
_INGEST_MAX_ATTRIBUTES_BYTES = 1024 * 1024


def _ingest_max_attributes_bytes() -> int:
    raw = os.environ.get('REGIN_INGEST_MAX_ATTR_BYTES')
    if raw:
        try:
            v = int(raw)
            if v > 0:
                return v
        except ValueError:
            pass
    return _INGEST_MAX_ATTRIBUTES_BYTES
