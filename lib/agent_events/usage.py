"""Project a `TurnCompleted` onto a `turn_usage` row.

Token spend is the one thing a session records that is not a span: it lands in
`turn_usage`, which the session aggregates read to derive cost, the context
meter and the window. So `to_span` returns None for the event and this module
is where it goes instead.

`turn_uuid` is the row's identity — the ingest upserts on
`(trace_id, turn_uuid)`. A hook-observed session uses the transcript entry's
uuid; an SDK-owned session has no transcript to key off at capture time, so the
turn's ordinal within the run serves the same purpose: stable across a replay
of the same turn, distinct across turns.
"""

from __future__ import annotations

from datetime import datetime

from .events import TurnCompleted


def turn_uuid(trace_id: str, turn_index: int) -> str:
    return f'{trace_id}:turn-{turn_index}'


def context_tokens(usage: dict | None) -> int:
    """Prompt size for one API call: input plus both cache counters
    (`lib/trace/transcript_usage`)."""
    usage = usage or {}
    return (int(usage.get('input_tokens') or 0)
            + int(usage.get('cache_read_tokens') or 0)
            + int(usage.get('cache_creation_tokens') or 0))


def turn_usage_row(
    event: TurnCompleted,
    turn_index: int,
    *,
    model: str | None = None,
    timestamp: str | None = None,
    context_used: int | None = None,
) -> dict:
    """One `turn_usage` row for a completed turn.

    The token counters are the turn's totals, summed by the CLI across every
    API call the turn made — correct for billing.

    `context_used_tokens` is **not** derivable from those totals: a turn that
    ran a tool loop sums `cache_creation` across its calls, so the naive sum
    reports roughly the whole turn's traffic as though it were one prompt
    (measured: 61,697 for a turn whose real prompt was 31,032). Pass
    `context_used` — the last API call's own prompt size — and the high-water
    mark the `/live` meter reads stays a context figure rather than a traffic
    figure. Falls back to the sum for a single-call turn, where they agree.
    """
    usage = event.usage or {}
    input_tokens = int(usage.get('input_tokens') or 0)
    cache_read = int(usage.get('cache_read_tokens') or 0)
    cache_creation = int(usage.get('cache_creation_tokens') or 0)
    return {
        'trace_id': event.trace_id,
        'turn_uuid': turn_uuid(event.trace_id, turn_index),
        'turn_index': turn_index,
        'timestamp': timestamp or datetime.now().isoformat(),
        'model': model,
        'input_tokens': input_tokens,
        'output_tokens': int(usage.get('output_tokens') or 0),
        'cache_read_tokens': cache_read,
        'cache_creation_tokens': cache_creation,
        'context_used_tokens': (
            context_used if context_used is not None
            else input_tokens + cache_read + cache_creation
        ),
    }
