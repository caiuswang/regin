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

import json
import os
import re
from datetime import datetime, timedelta
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


def has_ghost_agents(trace_id: str) -> bool:
    """True when the trace holds agent_id-tagged spans whose agent has NO
    subagent.start marker — the lost-marker signature. One EXISTS query so
    the live rescan can gate `reconstruct_subagent_markers` cheaply and
    skip fast when the trace is clean."""
    from lib.orm.engine import get_connection

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT EXISTS ("
            "  SELECT 1 FROM session_spans"
            "   WHERE trace_id = ?"
            "     AND json_extract(attributes, '$.agent_id') IS NOT NULL"
            "     AND json_extract(attributes, '$.agent_id') NOT IN ("
            "       SELECT json_extract(attributes, '$.agent_id')"
            "         FROM session_spans"
            "        WHERE trace_id = ? AND name = 'subagent.start'"
            "          AND json_extract(attributes, '$.agent_id') IS NOT NULL))",
            (trace_id, trace_id),
        ).fetchone()
        return bool(row and row[0])
    finally:
        conn.close()


def _agent_ids_with_start_marker(trace_id: str) -> set[str]:
    from lib.orm.engine import get_connection

    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT DISTINCT json_extract(attributes, '$.agent_id') AS aid "
            "  FROM session_spans "
            " WHERE trace_id = ? AND name = 'subagent.start'",
            (trace_id,),
        ).fetchall()
        return {r['aid'] for r in rows if r['aid']}
    finally:
        conn.close()


def _session_has_ended(trace_id: str) -> bool:
    from lib.orm.engine import get_connection

    conn = get_connection()
    try:
        row = conn.execute(
            'SELECT status FROM sessions WHERE trace_id = ?', (trace_id,),
        ).fetchone()
        return bool(row and row['status'] == 'ended')
    finally:
        conn.close()


def _agent_meta(transcript_path: str) -> dict:
    """The `agent-<id>.meta.json` sidecar Claude writes next to each subagent
    transcript: `{agentType, description, toolUseId, spawnDepth}`. The only
    durable source of the agent's type/description once the live markers and
    the tool.Agent launch span were lost."""
    import json

    meta_path = Path(transcript_path).with_suffix('').with_suffix('.meta.json')
    try:
        with open(meta_path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _synthesize_markers_for_agent(trace_id: str, agent_id: str,
                                  transcript_path: str, ended: bool) -> tuple[int, int]:
    """Synthesize the missing subagent.start (and, for an ended session, the
    subagent.stop) for one agent from its on-disk transcript. Returns
    `(starts_posted, stops_posted)`."""
    from hook_manager.handlers.subagent_lifecycle import (
        _normalize_subagent_ts, emit_subagent_responses,
    )
    from lib.hook_plugin import post_span
    from lib.trace.transcript_usage import read_usage

    usage = read_usage(transcript_path, max_text_bytes=0)
    turns = [t for t in (usage.turns if usage else []) if t.timestamp]
    if not turns:
        return 0, 0
    meta = _agent_meta(transcript_path)
    attrs: dict = {'agent_id': agent_id, 'synthesized': True}
    if meta.get('agentType'):
        attrs['agent_type'] = meta['agentType']
    if meta.get('description'):
        attrs['description'] = meta['description']
    first_ts = _normalize_subagent_ts(turns[0].timestamp)
    starts = int(post_span(
        trace_id=trace_id, name='subagent.start',
        span_id=f'substart-sa-{agent_id}',
        start_time=first_ts, end_time=first_ts, attributes=attrs,
    ))
    stops = 0
    # Only an ended session proves the agent is genuinely done; on a live
    # session liveness is undeterminable from the transcript alone, so
    # synthesize the start only and let status derive from activity.
    if ended:
        last_ts = _normalize_subagent_ts(turns[-1].timestamp)
        stops = int(post_span(
            trace_id=trace_id, name='subagent.stop',
            span_id=f'substop-sa-{agent_id}',
            start_time=last_ts, end_time=last_ts, attributes=dict(attrs),
        ))
    # The agent's internal turns were lost alongside the markers — replay
    # them from the same transcript (idempotent resp-sa-/think-sa- ids).
    emit_subagent_responses(trace_id, transcript_path, agent_id)
    return starts, stops


def reconstruct_subagent_markers(trace_id: str) -> dict:
    """Recover subagent.start/stop markers lost to an ingest outage.

    Markers (and tool.Agent launches) are the only span classes with no
    replay path: turn_trace re-emits the main transcript and subagent turns
    replay at SubagentStop, but SubagentStart/Stop hooks fire exactly once —
    an ingest failure loses them permanently, leaving "ghost" agents whose
    agent_id-tagged spans have no subagent.start to hang off. Synthesize the
    markers from the on-disk `subagents/agent-*.jsonl` transcripts
    (start = first turn ts, stop = last turn ts). Deterministic span ids
    (`substart-sa-<agent_id>`, mirroring `resp-sa-*`) keep repeated repairs
    idempotent; agents that already have a start marker are skipped.
    """
    from lib.trace.claude_subagents import _agent_transcripts

    agents = _agent_transcripts(trace_id)
    result = {'agents_on_disk': len(agents), 'starts_synthesized': 0,
              'stops_synthesized': 0}
    if not agents:
        return result
    have_start = _agent_ids_with_start_marker(trace_id)
    ended = _session_has_ended(trace_id)
    for agent_id, path in agents:
        if agent_id in have_start:
            continue
        try:
            starts, stops = _synthesize_markers_for_agent(
                trace_id, agent_id, path, ended)
        except Exception:
            _trace_log().error(
                'subagent_marker_reconstruct_failed',
                trace_id=trace_id, agent_id=agent_id, exc_info=True,
            )
            continue
        result['starts_synthesized'] += starts
        result['stops_synthesized'] += stops
    if result['starts_synthesized'] or result['stops_synthesized']:
        _trace_log().write(
            'subagent_markers_reconstructed', trace_id=trace_id, **result,
        )
    return result


# ── Transcript tool-span backfill ───────────────────────────────────
#
# The live rescan re-derives only assistant turns + subagent responses — it
# NEVER re-emits `tool.*` spans, so a server killed mid-tool permanently loses
# the PostToolUse span (stuck `pending-<tu>` placeholders, missing
# TaskCreate/TaskUpdate → a frozen task panel). This pass walks the transcript's
# assistant tool_use blocks + their user tool_result blocks and re-emits the
# missing `tool.*` spans through the SAME per-tool attribute builders the live
# PostToolUse handler uses. Deterministic span ids (`bftool-<tool_use_id>`) keep
# re-runs idempotent; carrying `tool_use_id` on the attrs lets merge.py retire
# the matching `pending-<tu>`/`permreq-<tu>` placeholder at read time exactly as
# a live resolution would. A tool_use with no transcript result yet (still
# running, or lost with no record) is SKIPPED — never fabricated as resolved —
# so the pass is safe to run on an active session.

INTERRUPT_SOURCE_USER = 'user'
_INTERRUPT_PREFIX = '[Request interrupted by user'
_TASK_CREATE_ID_RE = re.compile(r'Task #(\d+)')
_BACKFILL_SPAN_PREFIX = 'bftool-'


def _load_transcript_entries(path: str) -> list[dict]:
    out: list[dict] = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        return []
    return out


def _iter_tool_uses(entries: list[dict]):
    """Yield `(tool_use_id, name, input, assistant_ts)` for every assistant
    tool_use block, in transcript order."""
    for e in entries:
        if e.get('type') != 'assistant':
            continue
        ts = e.get('timestamp')
        for b in e.get('message', {}).get('content', []) or []:
            if (isinstance(b, dict) and b.get('type') == 'tool_use'
                    and isinstance(b.get('id'), str)):
                yield b['id'], b.get('name') or 'unknown', b.get('input') or {}, ts


def _tool_results(entries: list[dict]) -> dict:
    """`tool_use_id -> {content, is_error, ts}` from user tool_result blocks."""
    out: dict = {}
    for e in entries:
        if e.get('type') != 'user':
            continue
        ts = e.get('timestamp')
        content = e.get('message', {}).get('content')
        if not isinstance(content, list):
            continue
        for b in content:
            if isinstance(b, dict) and b.get('type') == 'tool_result':
                tuid = b.get('tool_use_id')
                if isinstance(tuid, str) and tuid:
                    out[tuid] = {'content': b.get('content'),
                                 'is_error': bool(b.get('is_error')), 'ts': ts}
    return out


def _result_is_interrupt(content) -> bool:
    if isinstance(content, str):
        return content.strip().startswith(_INTERRUPT_PREFIX)
    if isinstance(content, list):
        return any(
            isinstance(b, dict) and isinstance(b.get('text'), str)
            and b['text'].strip().startswith(_INTERRUPT_PREFIX)
            for b in content
        )
    return False


def _entry_has_interrupt_text(entry: dict) -> bool:
    content = entry.get('message', {}).get('content')
    if not isinstance(content, list):
        return False
    return any(
        isinstance(b, dict) and b.get('type') == 'text'
        and isinstance(b.get('text'), str)
        and b['text'].strip().startswith(_INTERRUPT_PREFIX)
        for b in content
    )


def _unresolved_tool_uses_in(entry: dict | None, results: dict) -> set:
    """tool_use_ids in one assistant entry that have no tool_result yet."""
    if not entry or entry.get('type') != 'assistant':
        return set()
    out = set()
    for b in entry.get('message', {}).get('content', []) or []:
        if (isinstance(b, dict) and b.get('type') == 'tool_use'
                and isinstance(b.get('id'), str) and b['id'] not in results):
            out.add(b['id'])
    return out


def _interrupted_tool_use_ids(entries: list[dict], results: dict) -> set:
    """tool_use_ids the USER interrupted, from two reliable signals: a
    tool_result whose content is the interrupt marker (the tuid is named), and
    a bare `[Request interrupted by user…]` text entry whose parentUuid resolves
    to an assistant turn holding a still-unresolved tool_use. The fuzzy
    "newest unresolved tool" guess is intentionally NOT used — a mislabelled
    interrupt is worse than leaving the pending for the stale-demotion sweep."""
    interrupted = {
        tuid for tuid, meta in results.items()
        if _result_is_interrupt(meta.get('content'))
    }
    by_uuid = {e.get('uuid'): e for e in entries}
    for e in entries:
        if e.get('type') == 'user' and _entry_has_interrupt_text(e):
            parent = by_uuid.get(e.get('parentUuid'))
            interrupted |= _unresolved_tool_uses_in(parent, results)
    return interrupted


def _synthetic_tool_response(tool: str, result_meta: dict | None) -> dict:
    """Best-effort structured tool_response for a builder. The transcript keeps
    only the rendered tool_result (a string or text blocks), NOT the rich
    `{stdout, file, task, …}` the live PostToolUse hook receives — so most
    builders get `{}` and fall back to input-derived attrs. TaskCreate is the
    exception: its `task_id` lives only in the result text (`Task #N created`),
    and the fold in `_fetch_session_task_list` drops any task event without one,
    so we recover it here."""
    if result_meta is None:
        return {}
    content = result_meta.get('content')
    if tool == 'TaskCreate' and isinstance(content, str):
        m = _TASK_CREATE_ID_RE.search(content)
        if m:
            return {'task': {'id': m.group(1)}}
    return content if isinstance(content, dict) else {}


def _backfill_span_attrs(tool: str, tool_input: dict, result_meta: dict | None,
                         agent_id: str | None, tuid: str, interrupted: bool) -> dict:
    from hook_manager.handlers.post_tool_trace import (
        _TOOL_BUILDERS, _build_mcp_attrs, _file_path,
    )
    attrs: dict = {'tool_name': tool, 'tool_use_id': tuid, 'backfilled': True}
    fp = _file_path(tool_input)
    if fp:
        attrs['file_path'] = fp
    tool_response = _synthetic_tool_response(tool, result_meta)
    builder = _TOOL_BUILDERS.get(tool)
    try:
        if builder is not None:
            builder(attrs, tool_input, tool_response, None)
        elif tool.startswith('mcp__'):
            _build_mcp_attrs(attrs, tool, tool_input, tool_response)
    except Exception:
        pass
    if agent_id:
        attrs['agent_id'] = agent_id
    if interrupted:
        attrs['is_interrupt'] = True
        attrs['interrupted'] = True
        attrs['interrupt_source'] = INTERRUPT_SOURCE_USER
    return attrs


def _norm_ts(ts):
    from hook_manager.handlers.subagent_lifecycle import _normalize_subagent_ts
    if not isinstance(ts, str) or not ts:
        return None
    try:
        return _normalize_subagent_ts(ts)
    except ValueError:
        return None


def _emit_backfill_span(trace_id, tuid, tool, tool_input, result_meta,
                        agent_id, interrupted, assistant_ts, span_id) -> bool:
    from lib.hook_plugin import post_span
    attrs = _backfill_span_attrs(
        tool, tool_input, result_meta, agent_id, tuid, interrupted)
    status = 'ERROR' if (interrupted or (result_meta and result_meta.get('is_error'))) else 'OK'
    start = _norm_ts(assistant_ts)
    end = _norm_ts(result_meta.get('ts')) if result_meta else None
    return bool(post_span(
        trace_id=trace_id, name=f'tool.{tool}', span_id=span_id,
        attributes=attrs, status_code=status,
        start_time=start, end_time=end or start,
    ))


def _resolved_tool_use_ids(trace_id: str) -> set:
    """tool_use_ids that already have a NON-pending `tool.*` span — the live
    PostToolUse spans (and prior backfills). Read from the attributes JSON,
    which the raw ingest always writes (the `tool_use_id` column is filled by a
    later attribution pass and may be NULL for freshly-ingested rows)."""
    from lib.orm.engine import get_connection

    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT json_extract(attributes, '$.tool_use_id') tu "
            "  FROM session_spans "
            " WHERE trace_id = ? AND status_code != 'PENDING' "
            "   AND name LIKE 'tool.%' "
            "   AND json_extract(attributes, '$.tool_use_id') IS NOT NULL",
            (trace_id,),
        ).fetchall()
        return {r[0] for r in rows if r[0]}
    finally:
        conn.close()


def has_stuck_pending_tools(trace_id: str, older_than_sec: int = 60) -> bool:
    """Cheap gate for the backfill: does the trace hold a PENDING
    `tool.*`/`permission.request` placeholder older than `older_than_sec`?
    One EXISTS narrowed by the `idx_session_spans_trace` index (there is no
    status_code index; the trace filter alone keeps it cheap). Placeholder
    start_times and `datetime.now()` are both naive-local ISO, so the lexical
    `<` is a valid time comparison."""
    from lib.orm.engine import get_connection

    cutoff = (datetime.now() - timedelta(seconds=older_than_sec)).isoformat()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT EXISTS (SELECT 1 FROM session_spans "
            " WHERE trace_id = ? AND status_code = 'PENDING' "
            "   AND (name LIKE 'tool.%' OR name = 'permission.request') "
            "   AND start_time < ?)",
            (trace_id, cutoff),
        ).fetchone()
        return bool(row and row[0])
    finally:
        conn.close()


def _backfill_one_transcript(trace_id: str, path: str, agent_id: str | None,
                             resolved: set, existing: set) -> int:
    entries = _load_transcript_entries(path)
    if not entries:
        return 0
    results = _tool_results(entries)
    interrupted = _interrupted_tool_use_ids(entries, results)
    posted = 0
    for tuid, tool, tool_input, a_ts in _iter_tool_uses(entries):
        if tuid in resolved:
            continue
        span_id = f'{_BACKFILL_SPAN_PREFIX}{tuid[:13]}'
        if span_id in existing:
            continue
        is_int = tuid in interrupted
        rmeta = results.get(tuid)
        # No transcript result and not interrupted → still running or lost with
        # no record. Never fabricate a resolved span; the read-time stale sweep
        # (merge._demote_stale_pending) covers the abandoned case.
        if not is_int and rmeta is None:
            continue
        if _emit_backfill_span(trace_id, tuid, tool, tool_input, rmeta,
                               agent_id, is_int, a_ts, span_id):
            posted += 1
            existing.add(span_id)
            resolved.add(tuid)
    return posted


def backfill_transcript_tool_spans(trace_id: str) -> dict:
    """Re-emit `tool.*` spans the live path lost, from the on-disk transcripts.

    Walks the main transcript and every subagent transcript, re-emitting any
    tool_use whose resolved span is missing (keyed by tool_use_id) using the
    live per-tool attribute builders. Idempotent: deterministic `bftool-<tu>`
    span ids + a skip on already-resolved tool_use_ids make repeated runs a
    no-op. Subagent tool spans are tagged with their `agent_id`. Returns
    `{trace_id, spans_backfilled, transcripts_walked}`."""
    result = {'trace_id': trace_id, 'spans_backfilled': 0, 'transcripts_walked': 0}
    main = _find_transcript(trace_id)
    if not main:
        return result
    resolved = _resolved_tool_use_ids(trace_id)
    existing = _existing_span_ids(trace_id)
    total = _backfill_one_transcript(trace_id, main, None, resolved, existing)
    result['transcripts_walked'] = 1
    try:
        from lib.trace.claude_subagents import _agent_transcripts
        for agent_id, sub_path in _agent_transcripts(trace_id):
            total += _backfill_one_transcript(
                trace_id, sub_path, agent_id, resolved, existing)
            result['transcripts_walked'] += 1
    except Exception:
        _trace_log().error('tool_span_backfill_subagents_failed',
                           trace_id=trace_id, exc_info=True)
    result['spans_backfilled'] = total
    if total:
        _trace_log().write('tool_spans_backfilled', trace_id=trace_id,
                           spans_backfilled=total,
                           transcripts_walked=result['transcripts_walked'])
    return result


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

    markers = reconstruct_subagent_markers(trace_id)

    # Recover tool.* spans (incl. TaskCreate/TaskUpdate and user-interrupted
    # calls) the live PostToolUse path lost — the turn re-emit above never
    # touches them.
    tool_backfill = backfill_transcript_tool_spans(trace_id)

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
        'subagent_markers': markers,
        'tool_backfill': tool_backfill,
    }
