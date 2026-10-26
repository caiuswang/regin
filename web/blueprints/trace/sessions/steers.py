"""Queued prompts and pending bridge steers shown on the session map."""

from __future__ import annotations

from lib.agent_bridge.pending import (  # noqa: F401
    _delivered_key, _is_represented, _partition_steers,
    _pending_bridge_steers, _represented_texts, _runner_queue as _sdk_queue,
    _session_has_ended, _steer_key, operator_queue,
)


def _queued_prompts(trace_id: str) -> list:
    return _dropped_steers(trace_id) + operator_queue(trace_id)


def _dropped_steers(trace_id: str) -> list:
    """Rows for prompts the session accepted and then abandoned.

    Served first, and never retired by an observed event: the send's ack had
    already cleared the composer, so this chip is the only copy of the text the
    operator still has. It leaves on their dismiss and on nothing else.
    """
    try:
        from lib.agent_bridge import store as bridge_store
        rows = bridge_store.list_dropped_steers(trace_id)
    except Exception:
        return []
    return [{'id': f"b{r['id']}", 'content': r['body'], 'source': 'dropped',
             'detail': r.get('delivery_detail') or '',
             'enqueued_at': r.get('created_at')} for r in rows]
