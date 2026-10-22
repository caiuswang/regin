"""Post the tool spans and session title a transcript itself carries.

Only formats that fill `TranscriptUsage.tool_spans` / `.title` (CodeBuddy)
reach this; for Claude and Kimi both are empty and nothing is posted.
"""

from __future__ import annotations

from .cache import _load_ai_title, _mark_seen, _save_ai_title
from .span_posters import _resolve_server_parent_id


def post_transcript_tool_spans(trace_id: str, usage, seen: set[str],
                               capture_text: bool) -> None:
    """A terminal span is cached as seen once it lands; a PENDING one is
    re-posted every scan (a duplicate id is a no-op) until its result shows
    up and the terminal span supersedes it."""
    if not usage.tool_spans:
        return
    from lib.hook_plugin import post_span  # type: ignore
    turns = {t.uuid: t for t in usage.turns}
    landed: list[str] = []
    for spec in usage.tool_spans:
        span_id = spec['span_id']
        if span_id in seen:
            continue
        turn = turns.get(spec.get('turn_uuid'))
        parent = _resolve_server_parent_id(turn, capture_text) if turn else None
        ok = post_span(
            trace_id=trace_id, span_id=span_id, name=spec['name'],
            parent_id=parent, start_time=spec['start_time'],
            end_time=spec['end_time'], duration_ms=spec['duration_ms'],
            attributes=dict(spec['attributes']), status_code=spec['status_code'],
        )
        if ok and spec['status_code'] != 'PENDING':
            landed.append(span_id)
    _mark_seen(trace_id, landed)


def post_transcript_title(trace_id: str, title: str | None,
                          start_time: str | None = None) -> None:
    """Same `sttl-` id and `claude_ai_title` source slot as Claude's
    `ai-title` line, so the title precedence in `ingest.py` (a user rename
    still wins) applies unchanged."""
    if not title:
        return
    source = 'claude_ai_title'
    cache_key = f'{source}:{title}'
    if cache_key == _load_ai_title(trace_id):
        return
    from lib.hook_plugin import post_span  # type: ignore
    if post_span(trace_id=trace_id, name='session.title',
                 span_id=f'sttl-{trace_id[:24]}',
                 attributes={'text': title, 'source': source},
                 start_time=start_time, end_time=start_time):
        _save_ai_title(trace_id, cache_key)
