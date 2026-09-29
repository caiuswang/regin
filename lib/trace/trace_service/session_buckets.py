"""Per-session counter buckets folded from a batch of ingested spans.

`_span_counter_buckets` turns normalised spans into one bucket per trace:
span counters, time bounds, title/model precedence and the start/end
markers the sessions upsert writes.
"""

from __future__ import annotations

import re

from lib.trace.session_tags import strip_prompt_tag_marker


def _is_less_specific_model(incoming: str, current: str | None) -> bool:
    """True when `incoming` is the bare base of a more-specific `current`,
    or when `incoming` is the placeholder `<synthetic>` that Claude Code
    writes for session init / /compact entries.

    Claude Code's SessionStart hook payload carries the variant suffix
    (e.g. ``claude-opus-4-7[1m]``) but the transcript JSONL always writes
    `message.model` as the bare base (``claude-opus-4-7``). The
    transcript-derived `turn` / `turn.usage` spans therefore pose a
    downgrade risk — this guard keeps the richer value.
    """
    # <synthetic> is never a real model and must never overwrite one.
    if incoming == '<synthetic>':
        return bool(current) and current != '<synthetic>'
    if not current:
        return False
    if incoming == current:
        return False
    if current.startswith(incoming + '['):
        return True
    # A launch sheet offers model *aliases* (`opus`), which the CLI resolves to
    # a concrete build (`claude-opus-5`). An SDK run stamps the alias on
    # session.start before the agent speaks, and a relaunch under a reused
    # trace id re-emits it — so the alias must never displace the resolved id
    # it names. A bare single-token id that appears as a segment of `current`
    # is that alias; anything hyphenated is already a full id.
    if '-' in incoming:
        return False
    return incoming in current.split('[')[0].split('-')


_TITLE_MAX_CHARS = 400


def _trim_title(text) -> str | None:
    """Collapse a session-title source to one short line.

    The first prompt of a session is often hundreds-to-thousands of chars
    of multi-paragraph instructions; storing it verbatim blows up the
    sessions-list row (one observed case: 39 792 chars). Take the first
    non-empty line and cap at `_TITLE_MAX_CHARS`, appending `…` when cut.
    Also applied defensively to `session.title` spans in case a future
    transcript carries a runaway custom title.
    """
    if not isinstance(text, str):
        return None
    first_line = next((ln.strip() for ln in text.splitlines() if ln.strip()), None)
    if not first_line:
        return None
    if len(first_line) > _TITLE_MAX_CHARS:
        return first_line[:_TITLE_MAX_CHARS].rstrip() + '…'
    return first_line


def _new_session_bucket() -> dict:
    return {
        'span_count': 0, 'skill_reads': 0, 'file_edits': 0,
        'rule_checks': 0, 'plan_enters': 0, 'prompts': 0,
        'tool_calls': 0, 'is_test': 0, 'test_name': None,
        'started_at': None, 'last_seen': None,
        'title': None, '_title_start': None,
        'live_title': None, 'live_title_source': None,
        'last_start_at': None,
        'ended_at': None, 'ended_reason': None,
        'agent_type': None, '_agent_type_start': None,
        'model': None, '_model_start': None,
        'cwd': None, '_cwd_start': None,
    }


# Span-name → counter-field. Mutually-exclusive single-counter mapping;
# the `tool.`/`pre_tool.` prefix counter is additive and handled
# separately so a `tool.Bash` span bumps `tool_calls` but no other
# counter. The file-edit tool spans (`tool.Edit`, `tool.Write`, …) map
# here for `file_edits` AND still bump `tool_calls` via the prefix rule.
#
# Reads span three disjoint signals — a skill content.md Read
# (`skill.read`), a slash-command expansion (`skill.invoke`), and an
# assistant `Skill` tool launch (`skill.launch`). `plan_enters` is no
# longer derived here: the Sessions list computes plans live from the
# `plan_sessions` table (see web/blueprints/trace/sessions.py).
_NAME_TO_COUNTER = {
    'skill.read': 'skill_reads',
    'skill.invoke': 'skill_reads',
    'skill.launch': 'skill_reads',
    'tool.Edit': 'file_edits',
    'tool.Write': 'file_edits',
    'tool.MultiEdit': 'file_edits',
    'tool.NotebookEdit': 'file_edits',
    'tool.apply_patch': 'file_edits',
    'rule.check': 'rule_checks',
    'prompt': 'prompts',
}


def _bump_named_counter(bucket: dict, name: str) -> None:
    counter = _NAME_TO_COUNTER.get(name)
    if counter:
        bucket[counter] += 1
    if name.startswith('tool.') or name.startswith('pre_tool.'):
        bucket['tool_calls'] += 1


def _apply_test_markers(bucket: dict, attrs: dict) -> None:
    if attrs.get('is_test'):
        bucket['is_test'] = 1
    tn = attrs.get('test_name')
    if tn and not bucket['test_name']:
        bucket['test_name'] = tn


def _update_time_bounds(bucket: dict, start, end) -> None:
    if start and (bucket['started_at'] is None or start < bucket['started_at']):
        bucket['started_at'] = start
    if end and (bucket['last_seen'] is None or end > bucket['last_seen']):
        bucket['last_seen'] = end


_SLASH_INVOCATION_RE = re.compile(r'^/[a-zA-Z][\w-]*(\s.*)?$')


def _skip_slash_invocation(text: object) -> object:
    """`text` with a leading bare `/skill args` line dropped, when content follows.

    A task armed with a skill puts the invocation on its own first line
    (`lib/tasks/runs.py::_invocation`) ahead of the actual idea; taking that
    line as the title collapses every such session's title to the skill name
    (e.g. every `/goal-verified-treenav` run reads the same). Dropped only
    when a non-blank line follows, so a bare command with nothing after it
    (never spawned with a body) still gets a title.
    """
    if not isinstance(text, str):
        return text
    lines = text.splitlines()
    idx = next((i for i, ln in enumerate(lines) if ln.strip()), None)
    if idx is None or not _SLASH_INVOCATION_RE.match(lines[idx].strip()):
        return text
    rest = '\n'.join(lines[idx + 1:])
    return rest if any(ln.strip() for ln in rest.splitlines()) else text


def _handle_prompt_title(bucket: dict, attrs: dict, start) -> None:
    """Earliest prompt by start_time becomes the 'first_prompt' title.

    A leading `regin-tags:` auto-tag marker line is stripped first so the
    title reflects the real instruction, not the metadata line; a leading
    bare skill invocation is skipped next for the same reason."""
    trimmed = _trim_title(
        _skip_slash_invocation(strip_prompt_tag_marker(attrs.get('text'))))
    if not trimmed:
        return
    if bucket['_title_start'] is None or (start and start < bucket['_title_start']):
        bucket['title'] = trimmed
        bucket['_title_start'] = start


def _handle_session_title(bucket: dict, attrs: dict, start) -> None:
    """Title surfaced from the Claude Code transcript — either a
    `custom-title` line (user ran /rename → `user_rename` source) or an
    `ai-title` line (Claude's auto-generated → `claude_ai_title`). The
    hook overwrites a stable span_id, so only the latest value lands in
    the spans table. Within one batch, a `user_rename` always beats
    `claude_ai_title` even if the ai-title is processed later.
    """
    src = attrs.get('source')
    trimmed = _trim_title(attrs.get('text'))
    if not (trimmed and src in ('user_rename', 'claude_ai_title')):
        return
    if bucket['live_title_source'] != 'user_rename' or src == 'user_rename':
        bucket['live_title'] = trimmed
        bucket['live_title_source'] = src


def _apply_timed_attr(
    bucket: dict, attrs: dict, attr_key: str, value_field: str,
    tracker_field: str, start, *, latest: bool, store_stripped: bool,
) -> None:
    """Set a session-start string attribute under a time-precedence rule.

    `latest=True` keeps the value seen at the greatest `start` (latest
    wins); `latest=False` keeps the earliest. The decision is tracked
    against `tracker_field`, never `value_field`, so a fallback that
    pre-set the value without a tracker timestamp is still overridden.
    `store_stripped` writes the whitespace-trimmed value (agent_type,
    cwd); model stores the raw string.
    """
    raw = attrs.get(attr_key)
    if not (isinstance(raw, str) and raw.strip()):
        return
    tracker = bucket[tracker_field]
    if latest:
        wins = tracker is None or (start and start > tracker)
    else:
        wins = tracker is None or (start and start < tracker)
    if not wins:
        return
    bucket[value_field] = raw.strip() if store_stripped else raw
    bucket[tracker_field] = start


def _apply_model(bucket: dict, model, start) -> None:
    """Latest-wins model application, guarded against a downgrade.

    Shared by session.start and the transcript carriers so the two cannot
    diverge: the bare base of a variant-bracketed id, and the launch alias an
    SDK run stamps before the agent speaks, both arrive later in time than the
    richer value they name and would otherwise win on timestamp alone.
    """
    if not (isinstance(model, str) and model.strip()):
        return
    if bucket['_model_start'] is not None and (
            not start or start <= bucket['_model_start']):
        return
    if _is_less_specific_model(model, bucket['model']):
        return
    bucket['model'] = model
    bucket['_model_start'] = start


def _handle_session_start(bucket: dict, attrs: dict, start) -> None:
    """session.start carries `last_start_at` (latest wins), `agent_type`
    (earliest non-empty wins), and `model` (latest wins, downgrade-guarded)."""
    if start and (bucket['last_start_at'] is None or start > bucket['last_start_at']):
        bucket['last_start_at'] = start
    _apply_timed_attr(bucket, attrs, 'agent_type', 'agent_type',
                      '_agent_type_start', start, latest=False, store_stripped=True)
    _apply_model(bucket, attrs.get('model'), start)
    _apply_timed_attr(bucket, attrs, 'cwd', 'cwd',
                      '_cwd_start', start, latest=False, store_stripped=True)


def _handle_transcript_model(bucket: dict, attrs: dict, start) -> None:
    """`turn` and `assistant_response` spans report the model from the
    transcript JSONL — same 'latest wins' rule as session.start, but the
    bare-base form must not downgrade a variant-bracketed model already
    on the bucket.

    `assistant_response` is the only model carrier for transcript-replayed
    sessions (internal `llm-stage` agent runs, resumes) that never emit a
    live `turn` model span and whose `session.start` has no model — without
    it their `sessions.model` stays NULL and the context window falls back
    to the 200K default. Parent-trace `assistant_response` spans belong to
    the main agent only (Task subagents write isolated sibling transcripts),
    so this can't pull a subagent's model onto the session.

    Kimi subagent turns, however, ARE emitted as `assistant_response`
    spans under the parent trace_id (`kimi_subagents._emit_subagent_turns`)
    — they carry an `agent_id` the main-agent turns never have, so skip
    them: a subagent's model must not overwrite the parent session's."""
    if attrs.get('agent_id'):
        return
    _apply_model(bucket, attrs.get('model'), start)


def _handle_session_end(bucket: dict, attrs: dict, start) -> None:
    if not start or (bucket['ended_at'] is not None and start <= bucket['ended_at']):
        return
    bucket['ended_at'] = start
    reason = attrs.get('reason')
    if isinstance(reason, str):
        bucket['ended_reason'] = reason


_PER_NAME_HANDLERS = {
    'prompt': _handle_prompt_title,
    'session.title': _handle_session_title,
    'session.start': _handle_session_start,
    'turn': _handle_transcript_model,
    'assistant_response': _handle_transcript_model,
    'session.end': _handle_session_end,
}


def _span_counter_buckets(spans, duplicates) -> dict:
    """Bucket newly-inserted spans by trace_id into counter deltas.

    Duplicates (already-present (trace_id, span_id) pairs) are skipped
    so an ingest retry never double-counts. Pure function — no I/O.
    """
    buckets: dict = {}
    for span, attrs in spans:
        key = (span.get('trace_id'), span.get('span_id'))
        if key in duplicates:
            continue
        tid = span.get('trace_id')
        if tid is None:
            continue
        bucket = buckets.setdefault(tid, _new_session_bucket())
        name = span.get('name') or ''
        bucket['span_count'] += 1
        _bump_named_counter(bucket, name)
        _apply_test_markers(bucket, attrs)

        start = span.get('start_time')
        end = span.get('end_time') or start
        _update_time_bounds(bucket, start, end)

        # Fallback agent_type from any span attribute, not just
        # session.start. SessionStart isn't guaranteed (resume / compact
        # in some Claude Code versions skips it), but every hook payload
        # already knows the agent — if a handler attached it, take it.
        # The `bucket['agent_type'] is None` guard means this is one
        # branch-not-taken per span after the first hit; session.start
        # still wins because its handler runs below with earliest-time
        # precedence.
        if bucket['agent_type'] is None:
            at = attrs.get('agent_type')
            if isinstance(at, str):
                at_stripped = at.strip()
                if at_stripped:
                    bucket['agent_type'] = at_stripped

        handler = _PER_NAME_HANDLERS.get(name)
        if handler is not None:
            handler(bucket, attrs, start)
    return buckets
