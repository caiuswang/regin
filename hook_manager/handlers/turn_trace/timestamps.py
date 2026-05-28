"""Timestamp normalisation helpers shared across the turn_trace package.

The server's `_widen_envelopes` mixes incoming timestamps with the
existing naive `start_time` column, and a raw Z-suffixed value trips
"can't compare offset-naive and offset-aware". So everything turn_trace
posts goes through one of these helpers first.
"""

from __future__ import annotations

from datetime import datetime


def _normalise_attachment_ts(ts: str | None) -> str | None:
    """Match the timestamp shape the rest of turn_trace uses (offset-
    naive local) so attachment spans sort alongside turn/tool spans
    instead of landing in a different timezone bucket."""
    if not isinstance(ts, str) or not ts:
        return None
    if ts.endswith('Z'):
        dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
        return dt.astimezone().replace(tzinfo=None).isoformat()
    return ts


def _to_naive_datetime(ts: str | None):
    """Parse a transcript timestamp string into an offset-naive local
    datetime, or None on failure. Used when we need to derive a few
    timestamps from one source instant (e.g. staggering server-tool
    spans within a turn).
    """
    if not isinstance(ts, str) or not ts:
        return None
    try:
        if ts.endswith('Z'):
            dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
            return dt.astimezone().replace(tzinfo=None)
        return datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None
