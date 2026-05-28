"""Spark-bucket helpers for the rule-triggers list endpoint.

The /trace/triggers tab renders a tiny histogram of fires-over-time
next to each rule. Bucket granularity adapts to the user's Range
filter so a 24h view shows hourly bars and a 30d view shows daily.

SQLite stores `rule_triggers.checked_at` as an ISO-8601 string from
`datetime('now')`. `strftime(pattern, checked_at)` produces a bucket
key on the SQL side; the Python side then zero-fills missing keys.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

# (sqlite_strftime_pattern, bucket_count, bucket_seconds)
#   24h → 24 hourly buckets   (1h each)
#   7d  →  7 daily buckets    (1d each)
#   30d → 30 daily buckets    (1d each)
#   all → 12 weekly buckets   (12 weeks back from now)
_RANGE_TO_BUCKET: dict[str, tuple[str, int, int]] = {
    "24h": ("%Y-%m-%dT%H", 24,  60 * 60),
    "7d":  ("%Y-%m-%d",     7,  24 * 60 * 60),
    "30d": ("%Y-%m-%d",    30,  24 * 60 * 60),
    "all": ("%Y-%W",       12,  7 * 24 * 60 * 60),
}

VALID_RANGES = tuple(_RANGE_TO_BUCKET)


def bucket_for_range(range_str: str) -> tuple[str, int, int]:
    """Return (strftime_pattern, bucket_count, bucket_seconds) for a range."""
    if range_str not in _RANGE_TO_BUCKET:
        raise ValueError(
            f"unknown range {range_str!r}; expected one of {VALID_RANGES}"
        )
    return _RANGE_TO_BUCKET[range_str]


def window_start_iso(range_str: str, now: datetime | None = None) -> str:
    """ISO-8601 timestamp marking the earliest row included in this range.

    Used to build the `WHERE checked_at >= ?` clause on the spark and
    aggregate queries so a single bound covers both.
    """
    pattern, count, secs = bucket_for_range(range_str)
    now = now or datetime.now(timezone.utc)
    return (now - timedelta(seconds=count * secs)).isoformat(sep=" ")


def expected_bucket_keys(
    range_str: str, now: datetime | None = None,
) -> list[str]:
    """The exact bucket keys, oldest → newest, that should appear in the spark.

    Caller maps `{bucket_key: count}` against this list to produce a
    zero-filled, fixed-length array.
    """
    pattern, count, secs = bucket_for_range(range_str)
    now = now or datetime.now(timezone.utc)
    keys: list[str] = []
    # oldest bucket sits `count - 1` steps behind the newest (which is "now").
    for i in range(count - 1, -1, -1):
        moment = now - timedelta(seconds=i * secs)
        keys.append(moment.strftime(_strftime_to_python(pattern)))
    return keys


def zero_fill(
    counts_by_key: dict[str, int], range_str: str,
    now: datetime | None = None,
) -> list[int]:
    """Render `{bucket_key: count}` into the expected-bucket-keys order.

    Missing keys become 0. The output length always equals the
    bucket count for the range.
    """
    keys = expected_bucket_keys(range_str, now=now)
    return [int(counts_by_key.get(k, 0)) for k in keys]


def _strftime_to_python(sqlite_pattern: str) -> str:
    """SQLite strftime patterns are a subset of Python's, but `%W` differs:

    SQLite `%W` = week of year (00-53, Monday-first).
    Python `%W` = same — compatible.

    Translation table kept narrow because we only emit four patterns.
    """
    # Currently identical to the SQLite patterns we use.
    return sqlite_pattern


__all__ = [
    "VALID_RANGES",
    "bucket_for_range",
    "expected_bucket_keys",
    "window_start_iso",
    "zero_fill",
]
