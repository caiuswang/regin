"""Self-heal for assistant_response / assistant.thinking / harness.* spans
and synthesized tool spans that got locked out by turn_trace's seen-uuid
cache.

Background: `hook_manager/handlers/turn_trace.py` keeps a per-session
`~/.local/share/regin/turn_trace_state/<trace_id>.txt` of uuids it has
already attempted to ingest. Older versions appended uuids
unconditionally, so any transient ingest failure (web server down,
payload rejected, race during transcript flush) permanently locked
those turns out — subsequent hook fires skip them.

The post-fix handler only caches on a successful post, so new losses
are bounded to one hook fire. This module recovers the historical loss:
walk the transcript, find every cached uuid whose expected span set is
incomplete in `session_spans`, drop them from the cache, then re-run
the same emission path the live hook would have taken. Idempotent
because span ingest uses INSERT OR REPLACE on (trace_id, span_id).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

from lib.activity_log import get_activity_logger as _get_activity_logger


def _trace_log():
    return _get_activity_logger("trace_ingest")


def _tool_call_expected_span_id(tc: dict) -> str | None:
    """The synthetic span_id turn_trace emits for one tool_call, or None
    when the call leaves no span (e.g. a non-error local tool, which
    PostToolUse covers). Keyed on `tool_use_id`, not the turn uuid."""
    from hook_manager.handlers import turn_trace as _tt

    tu_id = tc.get('id')
    tool_name = tc.get('name')
    if not isinstance(tu_id, str) or not isinstance(tool_name, str):
        return None
    if tc.get('server_side'):
        return f'srvtool-{tu_id[:13]}'
    if not tc.get('is_error'):
        return None
    result_text = tc.get('result_text')
    if _tt._is_permission_deny(result_text):
        prefix = 'askdeny-' if tool_name == 'AskUserQuestion' else 'tooldeny-'
        return f'{prefix}{tu_id[:13]}'
    if _tt._is_tool_use_error(result_text):
        return f'toolerr-{tu_id[:13]}'
    return None


def _turn_expected_span_ids(turn, capture_text: bool) -> set[str]:
    """Span_ids turn_trace emits for one assistant turn, plus any
    synthetic tool spans keyed on `tool_use_id`. A turn can emit BOTH a
    `resp-` (text) and a `think-` (extended thinking) span — they're
    independent, so a turn with both must list both here or repair would
    see `resp-` present and never re-emit the missing `think-`. Mirrors
    `span_posters._maybe_emit_assistant_span`."""
    span_ids: set[str] = set()
    if capture_text and turn.text:
        span_ids.add(f'resp-{turn.uuid[:13]}')
    if capture_text and turn.thinking_blocks:
        span_ids.add(f'think-{turn.uuid[:13]}')
    for tc in turn.tool_calls:
        sid = _tool_call_expected_span_id(tc)
        if sid:
            span_ids.add(sid)
    return span_ids


def _queued_command_expected_span_id(att) -> str | None:
    """`prompt-<uuid[:13]>` for a prompt-mode queued command, else None.
    Mirrors `span_posters._post_queued_command_span`."""
    from lib.trace.transcript_usage import queued_prompt_content

    text, images = queued_prompt_content(att.payload or {})
    if text or images:
        return f'prompt-{att.uuid[:13]}'
    return None


def _attachment_expected_span_ids(trace_id: str, att) -> set[str]:
    """Span_ids turn_trace emits for one traced attachment. Mirrors
    `span_posters._ATTACHMENT_HANDLERS`; an unhandled/skip case returns
    the empty set so the uuid isn't falsely unlocked on every repair."""
    if att.kind in ('task_reminder', 'deferred_tools_delta'):
        return {f'att-{att.uuid[:13]}'}
    if att.kind == 'skill_listing':
        payload = att.payload or {}
        is_initial = bool(payload.get('is_initial') or payload.get('isInitial'))
        return {f'skill-init-{trace_id[:24]}' if is_initial else f'att-{att.uuid[:13]}'}
    if att.kind == 'queued_command':
        sid = _queued_command_expected_span_id(att)
        return {sid} if sid else set()
    return set()


def _add_local_command_expected(out: dict[str, set[str]], local_commands) -> None:
    """Local-command spans use the command-name entry's uuid for the
    `cmd-*` span_id but mark caveat + stdout uuids as seen too. All three
    uuids share one expected span row — otherwise repair would unlock the
    caveat/stdout uuids forever (their own uuid never appears in a
    span_id)."""
    for lc in local_commands:
        expected = {f'cmd-{lc.command_uuid[:13]}'}
        out[lc.command_uuid] = expected
        if lc.stdout_uuid:
            out[lc.stdout_uuid] = expected
        if lc.caveat_uuid:
            out[lc.caveat_uuid] = expected


def _add_turn_expected(out: dict[str, set[str]], usage, capture_text: bool) -> None:
    """Record each turn's expected spans, keyed by the assistant uuid, plus
    its turn-anchor `prompt-<prompt_uuid>` keyed by the triggering user
    entry so a cached-but-missing anchor gets unlocked for re-emission."""
    for turn in usage.turns:
        if not turn.uuid:
            continue
        out[turn.uuid] = _turn_expected_span_ids(turn, capture_text)
        if turn.prompt_uuid and turn.prompt_uuid in usage.prompt_texts:
            out.setdefault(turn.prompt_uuid, set()).add(
                f'prompt-{turn.prompt_uuid[:13]}'
            )


def _expected_span_ids_by_uuid(trace_id: str, transcript_path: str) -> dict[str, set[str]]:
    """Return the span_ids turn_trace would emit for each cached uuid.

    The seen-cache is keyed by transcript-entry uuid, not by span_id. For
    simple cases (`assistant_response`, `assistant.thinking`,
    `hook.stop_summary`, most attachments) the emitted span_id is derived
    from that same uuid. But synthetic tool spans (`srvtool-*`,
    `askdeny-*`, `tooldeny-*`, `toolerr-*`) key off the nested
    `tool_use_id` instead. A turn can therefore have one present span
    (`resp-*`) while still missing a child tool span — the historical bug
    this repair path needs to recover.
    """
    from lib.settings import settings
    from lib.trace.transcript_usage import read_usage

    capture_text = bool(getattr(settings, 'capture_assistant_response', True))
    max_text_bytes = int(getattr(settings, 'assistant_response_max_bytes', 50_000) or 0)
    usage = read_usage(
        transcript_path,
        max_text_bytes=max_text_bytes if capture_text and max_text_bytes > 0 else None,
    )
    if usage is None:
        return {}

    out: dict[str, set[str]] = {}

    _add_turn_expected(out, usage, capture_text)

    for att in usage.attachments:
        out[att.uuid] = _attachment_expected_span_ids(trace_id, att)

    for ev in usage.system_events:
        spanned = ev.subtype in ('stop_hook_summary', 'away_summary')
        out[ev.uuid] = {f'sys-{ev.uuid[:13]}'} if spanned else set()

    _add_local_command_expected(out, usage.local_commands)

    return out


def _find_transcript(trace_id: str) -> str | None:
    """Locate the active provider's JSONL transcript for a session.

    Claude writes one file per session under
    `<transcript_projects_dir>/<munged_cwd>/<session_id>.jsonl`, so we
    scan all project subdirs for the filename match. Returns the first
    hit or None.
    """
    from lib.providers import get_active_provider

    base = get_active_provider().transcript_projects_dir()
    if not base or not Path(base).is_dir():
        return None
    target = f'{trace_id}.jsonl'
    for project_dir in Path(base).iterdir():
        if not project_dir.is_dir():
            continue
        candidate = project_dir / target
        if candidate.is_file():
            return str(candidate)
    return None


def _state_path(trace_id: str) -> Path:
    """Mirror turn_trace.py's _state_dir() resolution so we read/write
    the same cache file the live handler uses."""
    override = os.environ.get('REGIN_TURN_TRACE_STATE_DIR')
    if override:
        base = Path(override)
    else:
        base = Path.home() / '.local' / 'share' / 'regin' / 'turn_trace_state'
    return base / f'{trace_id}.txt'


def _existing_span_ids(trace_id: str) -> set[str]:
    """Snapshot of span_ids already in the DB for this trace. Cheap
    enough — a few hundred to a few thousand rows."""
    from lib.orm.engine import get_connection

    conn = get_connection()
    try:
        rows = conn.execute(
            'SELECT span_id FROM session_spans WHERE trace_id = ?',
            (trace_id,),
        ).fetchall()
        return {r['span_id'] for r in rows}
    finally:
        conn.close()


def _load_cache(path: Path) -> list[str]:
    if not path.is_file():
        return []
    try:
        with open(path) as f:
            return [line.strip() for line in f if line.strip()]
    except OSError:
        return []


def _save_cache(path: Path, uuids: Iterable[str]) -> None:
    """Atomic-ish rewrite: write to tmp then rename. Keeps the live
    handler from reading a half-truncated file mid-repair.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    with open(tmp, 'w') as f:
        for u in uuids:
            f.write(u + '\n')
    os.replace(tmp, path)


def _should_reemit(uuid: str, expected: set[str] | None, existing: set[str]) -> bool:
    """Whether a cached uuid must be unlocked so the live emit pass
    re-posts its spans.

    Prompt-anchor uuids are **always** re-emitted: an older live
    UserPromptSubmit hook clobbered the previous prompt's `prompt-<uuid>`
    anchor with the next prompt's text + a now() timestamp (off-by-one).
    The anchor row is therefore *present but wrong*, so the normal
    "expected ⊆ existing → keep" rule would never re-post it. Re-emitting
    from the transcript overwrites it with the correct id+text+time.

    Everything else is re-emitted only when its expected spans are missing
    (the historical seen-cache lockout this module was built to recover).
    """
    if expected is None:
        return False
    if f'prompt-{uuid[:13]}' in expected:
        return True
    return not expected.issubset(existing)


def repair_session_spans(trace_id: str) -> dict:
    """Heal a session whose seen-uuid cache locked out its
    assistant_response / assistant.thinking / harness.* spans.

    Steps:
      1. Locate the transcript and existing span_id set for the trace.
      2. Walk the cache; keep only uuids that already have at least one
         post-target span in the DB. Dropped uuids will be re-processed
         on the next emit pass.
      3. Re-run turn_trace's emit pipeline with the filtered seen-set,
         posting the missing spans via the regular ingest path.

    Returns a dict suitable for a JSON HTTP response, with counts so
    the caller can show "recovered N spans" in the UI. Raises only on
    truly unexpected failures (missing transcript, etc.) — callers
    surface 500.
    """
    transcript = _find_transcript(trace_id)
    if not transcript:
        return {
            'ok': False,
            'trace_id': trace_id,
            'error': f'no transcript found for {trace_id}',
        }

    cache_path = _state_path(trace_id)
    cached = _load_cache(cache_path)
    cached_unique = list(dict.fromkeys(cached))  # preserve order, dedupe

    existing = _existing_span_ids(trace_id)
    expected_by_uuid = _expected_span_ids_by_uuid(trace_id, transcript)
    kept: list[str] = []
    dropped: list[str] = []
    for u in cached_unique:
        expected = expected_by_uuid.get(u)
        if _should_reemit(u, expected, existing):
            dropped.append(u)
        else:
            kept.append(u)

    # Rewrite the cache so the live handler stops blocking these uuids,
    # *before* we re-run the emit — if the rewrite fails we'd rather
    # not emit and double-cache.
    _save_cache(cache_path, kept)

    # Reuse turn_trace's existing pipeline. Building a synthetic
    # payload avoids duplicating the read_usage + emit logic and keeps
    # this path on the same code as the live hook — bug fixes in one
    # place benefit both.
    from hook_manager.core import HookPayload
    from hook_manager.handlers import turn_trace as _tt

    payload = HookPayload(
        raw={'transcript_path': transcript},
        event='UserPromptSubmit',
        session_id=trace_id,
        tool_name=None,
    )
    _tt.handle(payload)

    # Persist the healed projection: the re-emit re-wrote the prompt
    # anchors and assistant_response/thinking spans with their correct
    # parentUuid-derived parents, so a materialize now durably records the
    # deterministic tree (parents + envelopes) instead of leaving it to
    # be recomputed on every read. Best-effort — a materialize failure
    # must not fail the repair, since the read path re-projects anyway.
    try:
        from lib.trace.trace_service import materialize_session
        materialize_session(trace_id)
    except Exception:
        _trace_log().error("span_repair_materialize_failed", exc_info=True)

    after = _existing_span_ids(trace_id)
    recovered = len(after - existing)
    _trace_log().write(
        "span_repair_completed",
        trace_id=trace_id, uuids_dropped=len(dropped),
        spans_recovered=recovered, transcript_path=transcript,
    )
    return {
        'ok': True,
        'trace_id': trace_id,
        'transcript_path': transcript,
        'cached_uuids_before': len(cached_unique),
        'cached_uuids_after': len(kept),
        'uuids_unlocked': len(dropped),
        'spans_recovered': recovered,
    }
