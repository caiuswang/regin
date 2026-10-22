"""Import a provider's on-disk transcripts into the trace store on demand.

Backs `regin trace import-transcripts`: sessions whose hooks never ran (a
WorkBuddy conversation from before regin's hooks were installed) still get
their spans and turn usage. It goes through the same posters as the hook
and live-rescan paths, so the span ids — and therefore idempotency — are the
ones those paths produce.
"""

from __future__ import annotations

import glob
from pathlib import Path

from lib.activity_log import get_activity_logger

log = get_activity_logger("trace_ingest")


def importable(provider) -> bool:
    return getattr(provider, 'transcript_format', 'claude') == 'codebuddy'


def transcript_paths(provider, session: str | None = None) -> list[str]:
    if session:
        found = provider.find_session_transcript(session)
        return [found] if found else []
    base = provider.transcript_projects_dir()
    return sorted(glob.glob(str(Path(base) / '*' / '*.jsonl')))


def _hook_resolved_calls(trace_id: str) -> set[str]:
    """tool_use_ids a hook already wrote a finished tool span for. Those calls
    keep the hook row alone: a transcript twin would count twice in every
    ingest-time aggregate."""
    from lib.orm.engine import get_connection
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT DISTINCT COALESCE(tool_use_id, json_extract(attributes, '$.tool_use_id')) "
            "FROM session_spans WHERE trace_id = ? AND name LIKE 'tool.%' "
            "AND status_code != 'PENDING' "
            "AND json_extract(attributes, '$.transcript_source') IS NULL",
            (trace_id,)).fetchall()
    finally:
        conn.close()
    return {r[0] for r in rows if r[0]}


def _without_hooked_calls(usage, trace_id: str):
    hooked = _hook_resolved_calls(trace_id)
    if not hooked:
        return usage
    from dataclasses import replace
    return replace(usage, tool_spans=tuple(
        s for s in usage.tool_spans
        if s['attributes'].get('tool_use_id') not in hooked))


def _trace_id(path: str) -> str:
    from lib.trace.codebuddy_transcript import transcript_session_id
    return transcript_session_id(path)


def import_session(provider, path: str) -> tuple[str | None, list[str]]:
    """Ingest one transcript under its own sessionId. Returns that id (None
    when the file holds nothing to ingest) and the posts the store rejected."""
    from hook_manager.handlers.turn_trace.entry import (
        _post_transcript_usage, _text_capture_config,
    )
    from lib import hook_plugin
    from lib.trace.in_process_ingest import in_process_ingest

    capture_text, max_text_bytes, read_cap = _text_capture_config()
    usage = provider.parse_transcript(path, max_text_bytes=read_cap)
    if usage is None:
        return None, []
    trace_id = _trace_id(path)
    usage = _without_hooked_calls(usage, trace_id)
    previous = hook_plugin._active_agent_type
    hook_plugin.set_active_agent_type(provider.provider_id)
    try:
        with in_process_ingest() as errors:
            _post_transcript_usage(trace_id, usage, None, capture_text=capture_text,
                                   max_text_bytes=max_text_bytes, effort_level=None,
                                   with_tool_spans=True)
    finally:
        hook_plugin.set_active_agent_type(previous)
    if errors:
        log.error("transcript_import_failed", provider=provider.provider_id,
                  trace_id=trace_id, path=path, errors=errors[:5])
    else:
        log.write("transcript_imported", provider=provider.provider_id,
                  trace_id=trace_id, path=path)
    return trace_id, errors


def import_transcripts(provider, session: str | None = None) -> dict:
    counts = {'imported': 0, 'empty': 0, 'failed': 0}
    for path in transcript_paths(provider, session):
        trace_id, errors = import_session(provider, path)
        if trace_id is None:
            counts['empty'] += 1
        elif errors:
            counts['failed'] += 1
        else:
            counts['imported'] += 1
    return counts
