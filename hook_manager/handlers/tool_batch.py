"""PostToolBatch → one `batch.tools` row naming the calls of a parallel batch.

The tool spans themselves are already written and the store is append-only, so
this row does not touch them: `lib.trace.merge` stamps `tool_batch_id` onto
each tool span whose `tool_use_id` the row lists, at read time.
"""

from __future__ import annotations

import hashlib

from ..core import HookPayload, HookResponse
from .misc_events import _safe_emit

TOOL_BATCH_SPAN = 'batch.tools'


def handle(payload: HookPayload) -> HookResponse | None:
    if payload.is_workflow_subagent:
        return None
    ids = _tool_use_ids(payload.raw or {})
    if not ids:
        return HookResponse(suppress_output=True)
    batch_id = batch_id_for(ids)
    _safe_emit(payload, TOOL_BATCH_SPAN,
               {'tool_batch_id': batch_id, 'tool_use_ids': ids},
               span_id=f'toolbatch-{batch_id}')
    return HookResponse(suppress_output=True)


def batch_id_for(tool_use_ids: list[str]) -> str:
    """Derived from the member ids alone, so a re-delivered batch lands on the
    same span id and batch id instead of minting a second batch."""
    digest = hashlib.sha256('\n'.join(sorted(tool_use_ids)).encode()).hexdigest()
    return digest[:16]


def _tool_use_ids(raw: dict) -> list[str]:
    calls = raw.get('tool_calls')
    if not isinstance(calls, list):
        return []
    ids = []
    for call in calls:
        tu_id = call.get('tool_use_id') if isinstance(call, dict) else None
        if isinstance(tu_id, str) and tu_id and tu_id not in ids:
            ids.append(tu_id)
    return ids
