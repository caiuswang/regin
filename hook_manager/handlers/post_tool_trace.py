"""Handler: PostToolUse → emit a `tool.{name}` span per call.

The model already sees tool input/response in its transcript, so this
handler does NOT return `additional_context` — that was transcript noise
(the `fa3922e` silent-trace policy). But the *trace DB* still needs the
data so the session-trace view can show tool activity under each prompt
span. This handler posts one span per tool call; full raw payloads stay
in `~/.claude/hook-payloads.jsonl` via `trace_payload`.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import uuid
from datetime import datetime, timedelta

from ..core import HookPayload, HookResponse

_PREVIEW_MAX = 200
# Inline caps for span attributes. Sized to safely exceed what Claude
# Code itself sends through the hook (empirically capped around 30 KB
# for tool output, ~64 KB for tool input) so truncation never fires in
# practice. The `_truncated_bytes` markers remain in the schema in case
# a future upstream change pushes data past these ceilings — but at
# today's observed traffic they record zero drops.
#
# command/diff are model-bounded (the model has to fit them in its
# output context), so we set generous ceilings rather than chase
# theoretical maxima.
_BASH_COMMAND_MAX = 256 * 1024
_BASH_STDOUT_MAX = 64 * 1024
_BASH_STDERR_MAX = 32 * 1024
_EDIT_DIFF_MAX = 256 * 1024
# Read tool result: file content the model just saw. Cap matches Bash
# stdout — the model's own context can't fit much more than this on a
# typical turn, so storing the full payload past this point is waste.
_READ_CONTENT_MAX = 64 * 1024


def _bash_preview(tool_input: dict) -> str:
    cmd = (tool_input or {}).get('command') or ''
    return cmd[:_PREVIEW_MAX] + ('…' if len(cmd) > _PREVIEW_MAX else '')


def _truncate_output(text: str, limit: int) -> tuple[str, int]:
    if not isinstance(text, str) or not text:
        return '', 0
    if len(text) <= limit:
        return text, 0
    return text[:limit], len(text) - limit


def _truncate_diff(text: str, limit: int) -> tuple[str, int]:
    if len(text) <= limit:
        return text, 0
    # Cut at the last newline before the limit so the truncated diff
    # doesn't end mid-line (which would render as a partial -/+ line).
    cut = text.rfind('\n', 0, limit)
    if cut < 0:
        cut = limit
    return text[:cut], len(text) - cut


def _compute_unified_diff(old: str, new: str) -> tuple[str, int, int]:
    """Return (diff_text, added_lines, removed_lines).

    File-header lines (`--- `/`+++ `) are stripped because the WebUI
    synthesizes its own header from `file_path` + `edit_op`. Output is
    newline-joined with no trailing newline.
    """
    old_lines = (old or '').splitlines()
    new_lines = (new or '').splitlines()
    hunk_lines: list[str] = []
    started = False
    for line in difflib.unified_diff(old_lines, new_lines, n=3, lineterm=''):
        if not started:
            if line.startswith('@@'):
                started = True
                hunk_lines.append(line)
            continue
        hunk_lines.append(line)
    diff_text = '\n'.join(hunk_lines)
    added = sum(1 for l in hunk_lines if l.startswith('+'))
    removed = sum(1 for l in hunk_lines if l.startswith('-'))
    return diff_text, added, removed


def _attach_edit_diff(attrs: dict, diff_text: str, added: int, removed: int, op: str) -> None:
    truncated, dropped = _truncate_diff(diff_text, _EDIT_DIFF_MAX)
    attrs['diff'] = truncated
    if dropped:
        attrs['diff_truncated_bytes'] = dropped
    attrs['added_lines'] = added
    attrs['removed_lines'] = removed
    attrs['edit_op'] = op


def _hunk_int(h: dict, *keys: str) -> int:
    # Claude Code emits hunk bounds in camelCase (`oldStart`); accept the
    # snake_case spelling too. `or`-style truthy fallback (0/''/None skip to
    # the next key, then default 0) is preserved deliberately — int() stays
    # here so a non-numeric value raises into the caller's per-hunk guard.
    for k in keys:
        v = h.get(k)
        if v:
            return int(v)
    return 0


def _parse_hunk(h: object) -> dict | None:
    # One structured_patch entry → normalized line-range dict, or None if it
    # is not a dict or any bound is non-numeric (drop the whole hunk, never a
    # partial). Matches the original try/except-per-hunk behavior.
    if not isinstance(h, dict):
        return None
    try:
        return {
            'old_start': _hunk_int(h, 'oldStart', 'old_start'),
            'old_lines': _hunk_int(h, 'oldLines', 'old_lines'),
            'new_start': _hunk_int(h, 'newStart', 'new_start'),
            'new_lines': _hunk_int(h, 'newLines', 'new_lines'),
        }
    except (TypeError, ValueError):
        return None


def _parse_hunks(patch: object) -> list[dict]:
    if not (isinstance(patch, list) and patch):
        return []
    hunks: list[dict] = []
    for h in patch:
        parsed = _parse_hunk(h)
        if parsed is not None:
            hunks.append(parsed)
    return hunks


def _attach_edit_metadata(attrs: dict, tool_response: dict) -> None:
    """Pull non-diff-derivable signals off `tool_response` and stash them
    on the span. `user_modified=True` means the user hand-edited the diff
    in the Claude Code UI before applying — high-signal for trace review
    because it explains divergences between what the model proposed and
    what actually landed. `replace_all` distinguishes a single-site Edit
    from a sweep. Hunk ranges (line numbers) make it possible for the
    trace UI to say "edited lines 740–752" without re-fetching the
    file.
    """
    if not isinstance(tool_response, dict):
        return
    if tool_response.get('user_modified') is True:
        attrs['user_modified'] = True
    if tool_response.get('replace_all') is True:
        attrs['replace_all'] = True
    hunks = _parse_hunks(tool_response.get('structured_patch'))
    if hunks:
        attrs['hunks'] = hunks


def _file_path(tool_input: dict) -> str | None:
    ti = tool_input or {}
    return ti.get('file_path') or ti.get('path') or ti.get('notebook_path')


def _ask_option(o: dict) -> dict:
    # The terminal renders each option as `<label> — <description>`
    # (and previews when present), so the trace must keep all three
    # fields to faithfully replay what the user actually saw.
    out: dict = {'label': o.get('label')}
    desc = o.get('description')
    if desc:
        out['description'] = desc
    preview = o.get('preview')
    if preview:
        out['preview'] = preview
    return out


def _normalize_tool_name(name: str) -> str:
    # MCP tools come as `mcp__server__tool` — collapse for readability but
    # keep the full name available as an attribute.
    return name


def _span_timing(raw: dict) -> tuple[int, str | None, str | None]:
    """Derive (duration_ms, start_time, end_time) from Claude Code's
    authoritative `duration_ms` (tool execution time). No PreToolUse span
    exists for tools, so without this the span would record duration 0.
    Anchor end at now and back-date start so the timeline stays
    self-consistent. Falls back to (0, None, None) — build_span then
    defaults both timestamps to now — for tool versions that omit it."""
    dur = raw.get('duration_ms')
    if not isinstance(dur, int) or dur < 0:
        return 0, None, None
    now = datetime.now()
    return dur, (now - timedelta(milliseconds=dur)).isoformat(), now.isoformat()


# ── Per-tool attribute builders ─────────────────────────────────
#
# Each builder mutates `attrs` in place with tool-specific fields. The
# common preamble (tool_name, tool_use_id, file_path) and postamble
# (agent_id, agent_type, post_span) live in `_emit_span` so they don't
# get duplicated across builders.


def _build_bash_input_attrs(attrs: dict, tool_input: dict) -> None:
    """Input-derived Bash attrs: `command_preview` (always) plus the full
    `command` when it exceeds the preview cap. Split out of `_build_bash_attrs`
    so the PreToolUse pending span can carry the *same* flat keys the resolved
    card reads (`traceFormatters.fullLabel` / BashCard) — otherwise the in-flight
    card has no `command_preview` and degrades to a bare "Bash" row."""
    attrs['command_preview'] = _bash_preview(tool_input)
    cmd_full = tool_input.get('command') or ''
    if isinstance(cmd_full, str) and len(cmd_full) > _PREVIEW_MAX:
        command, dropped_cmd = _truncate_output(cmd_full, _BASH_COMMAND_MAX)
        attrs['command'] = command
        if dropped_cmd:
            attrs['command_truncated_bytes'] = dropped_cmd


def _build_bash_attrs(attrs: dict, tool_input: dict, tool_response: dict, payload: HookPayload) -> None:
    _build_bash_input_attrs(attrs, tool_input)
    stdout, dropped_out = _truncate_output(tool_response.get('stdout'), _BASH_STDOUT_MAX)
    if stdout:
        attrs['stdout'] = stdout
        if dropped_out:
            attrs['stdout_truncated_bytes'] = dropped_out
    stderr, dropped_err = _truncate_output(tool_response.get('stderr'), _BASH_STDERR_MAX)
    if stderr:
        attrs['stderr'] = stderr
        if dropped_err:
            attrs['stderr_truncated_bytes'] = dropped_err
    if tool_response.get('interrupted'):
        attrs['interrupted'] = True
    _attach_bash_response_meta(attrs, tool_response)


# Tool-independent metadata Claude Code stamps on a Bash `tool_response`:
# a human-readable exit-status gloss, the background-task bookkeeping it
# emits when a command is (auto-)backgrounded, plus a timeout marker
# (`timed_out_after_ms`, 2.1.215+) and a sandbox-disabled flag
# (`dangerously_disable_sandbox`, 2.1.215+). Keys are read snake_case —
# `_normalize_payload` aliases the camelCase originals. Only truthy values
# are kept, so the common non-backgrounded, non-timed-out, sandboxed case
# adds nothing.
_BASH_RESPONSE_META: tuple[tuple[str, type], ...] = (
    ('return_code_interpretation', str),
    ('background_task_id', str),
    ('assistant_auto_backgrounded', bool),
    ('persisted_output_path', str),
    ('persisted_output_size', int),
    ('timed_out_after_ms', int),
    ('dangerously_disable_sandbox', bool),
)


def _attach_bash_response_meta(attrs: dict, tool_response: dict) -> None:
    for key, typ in _BASH_RESPONSE_META:
        val = tool_response.get(key)
        if isinstance(val, typ) and val:
            attrs[key] = val
    _attach_bash_git_commit(attrs, tool_response)


def _attach_bash_git_commit(attrs: dict, tool_response: dict) -> None:
    """Capture the structured git-commit result Claude Code (2.1.195+) stamps
    on a Bash `tool_response` when the command committed:
    `git_operation.commit.{sha, kind}`. Flattened to `git_commit_sha` /
    `git_op_kind` so the trace can badge commits made in a session. Read
    snake_case — `_normalize_payload` deep-aliases the camelCase `gitOperation`
    original inside `tool_response`. Nothing is added for non-git Bash calls."""
    git_op = tool_response.get('git_operation')
    if not isinstance(git_op, dict):
        return
    commit = git_op.get('commit')
    if not isinstance(commit, dict):
        return
    sha = commit.get('sha')
    if isinstance(sha, str) and sha:
        attrs['git_commit_sha'] = sha
    kind = commit.get('kind')
    if isinstance(kind, str) and kind:
        attrs['git_op_kind'] = kind


def _build_edit_attrs(attrs: dict, tool_input: dict, tool_response: dict, payload: HookPayload) -> None:
    diff, added, removed = _compute_unified_diff(
        tool_input.get('old_string') or '',
        tool_input.get('new_string') or '',
    )
    if diff:
        _attach_edit_diff(attrs, diff, added, removed, 'edit')
    _attach_edit_metadata(attrs, tool_response)


def _build_write_attrs(attrs: dict, tool_input: dict, tool_response: dict, payload: HookPayload) -> None:
    diff, added, removed = _compute_unified_diff('', tool_input.get('content') or '')
    if diff:
        _attach_edit_diff(attrs, diff, added, removed, 'write')
    _attach_edit_metadata(attrs, tool_response)


def _build_multiedit_attrs(attrs: dict, tool_input: dict, tool_response: dict, payload: HookPayload) -> None:
    parts: list[str] = []
    total_added = total_removed = 0
    for e in tool_input.get('edits') or []:
        if not isinstance(e, dict):
            continue
        d, a, r = _compute_unified_diff(e.get('old_string') or '', e.get('new_string') or '')
        if d:
            parts.append(d)
            total_added += a
            total_removed += r
    if parts:
        _attach_edit_diff(attrs, '\n'.join(parts), total_added, total_removed, 'multi_edit')
    _attach_edit_metadata(attrs, tool_response)


def _build_read_attrs(attrs: dict, tool_input: dict, tool_response: dict, payload: HookPayload) -> None:
    # Capture what the model actually saw: file content (capped), line
    # slice it read, and total file size. Without this the trace can
    # show "Read foo.py" but can't replay whether it got the whole file
    # or just lines 100-200.
    file_info = tool_response.get('file') if isinstance(tool_response.get('file'), dict) else None
    if not file_info:
        return
    content = file_info.get('content')
    if isinstance(content, str) and content:
        head, dropped = _truncate_output(content, _READ_CONTENT_MAX)
        attrs['content'] = head
        if dropped:
            attrs['content_truncated_bytes'] = dropped
    # snake_case wins over camelCase if both are present; the order in
    # each tuple is load-bearing.
    for attr_key, field_options in (
        ('num_lines', ('num_lines', 'numLines')),
        ('start_line', ('start_line', 'startLine')),
        ('total_lines', ('total_lines', 'totalLines')),
    ):
        for field in field_options:
            v = file_info.get(field)
            if isinstance(v, int):
                attrs[attr_key] = v
                break
    _attach_image_tokens(attrs, file_info)


def _attach_image_tokens(attrs: dict, file_info: dict) -> None:
    """For a Read of an image, Claude Code reports the post-downsample
    `displayWidth`/`displayHeight` it sent to the API. That's the
    authoritative billed size, so compute exact image tokens from it and
    stash `image_tokens_exact` — `ingest_tool_attribution` prefers it
    over the base64-header estimate. `image_dimensions` is kept for UI
    transparency. No-op when the Read wasn't an image."""
    dims = file_info.get('dimensions')
    if not isinstance(dims, dict):
        return
    dw = dims.get('displayWidth') or dims.get('display_width')
    dh = dims.get('displayHeight') or dims.get('display_height')
    if not (isinstance(dw, int) and isinstance(dh, int)):
        return
    from lib.tokens.token_estimator import estimate_image_tokens_from_dims
    attrs['image_tokens_exact'] = estimate_image_tokens_from_dims(dw, dh)
    attrs['image_dimensions'] = dims


def _build_pattern_attrs(attrs: dict, tool_input: dict, tool_response: dict, payload: HookPayload) -> None:
    """Glob and Grep both stash their search pattern under `pattern`."""
    pat = tool_input.get('pattern')
    if pat:
        attrs['pattern'] = pat


def _build_ask_attrs(attrs: dict, tool_input: dict, tool_response: dict, payload: HookPayload) -> None:
    questions = tool_input.get('questions') or []
    attrs['questions'] = [
        {
            'question': q.get('question'),
            'header': q.get('header'),
            'options': [_ask_option(o) for o in (q.get('options') or [])],
            'multiSelect': q.get('multiSelect', False),
        }
        for q in questions
    ]
    answers = tool_response.get('answers') or tool_input.get('answers') or {}
    if answers:
        attrs['answers'] = answers
    annotations = tool_response.get('annotations') or tool_input.get('annotations') or {}
    if annotations:
        attrs['annotations'] = annotations


def _build_toolsearch_attrs(attrs: dict, tool_input: dict, tool_response: dict, payload: HookPayload) -> None:
    # Two query shapes worth preserving:
    #   • `select:tool_a,tool_b,…` — exact tools being deferred-loaded
    #   • free text (`+slack send`, `"jupyter notebook"`) — keyword search
    # `select:` is the common case for MCP/deferred-tool loading, so we
    # also break out the parsed tool list to save the reader from re-
    # parsing the colon syntax in their head.
    q = tool_input.get('query')
    if isinstance(q, str) and q:
        attrs['query'] = q
        if q.startswith('select:'):
            names = [n.strip() for n in q[len('select:'):].split(',') if n.strip()]
            if names:
                attrs['selected_tools'] = names
    mr = tool_input.get('max_results')
    if isinstance(mr, int):
        attrs['max_results'] = mr
    m = tool_response.get('matches')
    if isinstance(m, list) and m:
        attrs['loaded_tools'] = [str(x) for x in m if isinstance(x, str)]
    tdt = tool_response.get('total_deferred_tools')
    if isinstance(tdt, int):
        attrs['total_deferred_tools'] = tdt


def _build_taskcreate_attrs(attrs: dict, tool_input: dict, tool_response: dict, payload: HookPayload) -> None:
    subject = tool_input.get('subject')
    if isinstance(subject, str) and subject:
        attrs['subject'] = subject
    description = tool_input.get('description')
    if isinstance(description, str) and description:
        head, dropped = _truncate_output(description, _BASH_STDOUT_MAX)
        attrs['description'] = head
        if dropped:
            attrs['description_truncated_bytes'] = dropped
    active_form = tool_input.get('activeForm')
    if isinstance(active_form, str) and active_form:
        attrs['active_form'] = active_form
    # Claude Code returns the new task_id inside `tool_response.task.id`
    # (the dedicated TaskCreate hook fixture); the older flat shape
    # `tool_response.task_id` is unlikely but handled as a fallback.
    task = tool_response.get('task') if isinstance(tool_response.get('task'), dict) else None
    task_id = (task.get('id') if task else None) or tool_response.get('task_id') or tool_response.get('taskId')
    if task_id is not None:
        attrs['task_id'] = str(task_id)


_WF_RUN_RE = re.compile(r'workflows/(wf_[A-Za-z0-9][A-Za-z0-9_-]*)')


def _build_workflow_attrs(attrs: dict, tool_input: dict, tool_response: dict, payload: HookPayload) -> None:
    """Stamp the launched run_id onto the `tool.Workflow` span at capture time.

    The Workflow tool launches a background run and returns its dir
    (`…/subagents/workflows/<run_id>`). Capturing `workflow_run_id` here — live,
    from the tool result — gives ingest a compaction-proof link to the run.
    Otherwise the only link is the call's `input.script`, which transcript
    compaction strips first, orphaning the run's subagents in the session view.
    """
    blob = tool_response if isinstance(tool_response, str) else json.dumps(tool_response)
    m = _WF_RUN_RE.search(blob or '')
    if m:
        attrs['workflow_run_id'] = m.group(1)


def _build_taskupdate_attrs(attrs: dict, tool_input: dict, tool_response: dict, payload: HookPayload) -> None:
    # Normalise camelCase taskId → snake_case so attribute access is
    # consistent across the codebase.
    task_id = tool_input.get('taskId') or tool_input.get('task_id')
    if task_id is not None:
        attrs['task_id'] = str(task_id)
    status = tool_input.get('status')
    if isinstance(status, str) and status:
        attrs['status'] = status


def _build_taskoutput_attrs(attrs: dict, tool_input: dict, tool_response: dict, payload: HookPayload) -> None:
    # `retrieval_status` ('success' / 'not_found' / 'timed_out') is
    # distinct from the wrapped task's `status` and matters for
    # block=false polls. The span's own status_code stays OK even when
    # the wrapped task exited non-zero — this tool call succeeded.
    task_id = tool_input.get('task_id') or tool_input.get('taskId')
    if task_id is not None:
        attrs['task_id'] = str(task_id)
    retrieval = tool_response.get('retrieval_status')
    if isinstance(retrieval, str) and retrieval:
        attrs['retrieval_status'] = retrieval
    task = tool_response.get('task') if isinstance(tool_response.get('task'), dict) else None
    if not task:
        return
    if 'task_id' not in attrs and task.get('task_id') is not None:
        attrs['task_id'] = str(task['task_id'])
    for field in ('task_type', 'status', 'description'):
        v = task.get(field)
        if isinstance(v, str) and v:
            attrs[field] = v
    exit_code = task.get('exit_code')
    if isinstance(exit_code, int):
        attrs['exit_code'] = exit_code
    output = task.get('output')
    if isinstance(output, str) and output:
        stored, dropped = _truncate_output(output, _BASH_STDOUT_MAX)
        attrs['output'] = stored
        if dropped:
            attrs['output_truncated_bytes'] = dropped


def _build_schedulewakeup_attrs(attrs: dict, tool_input: dict, tool_response: dict, payload: HookPayload) -> None:
    # ScheduleWakeup is always turn-final: the agent yields control at the end
    # of an autonomous (/loop) turn. `stop` ends the loop (agent finished);
    # otherwise it schedules a resume after `delay_seconds` (the tool emits
    # camelCase `delaySeconds`), with `reason` explaining why — often polling
    # background work, not "done". These three fields are what the trace UI
    # needs to tell "finished" from "paused to resume".
    stop = tool_input.get('stop')
    if isinstance(stop, bool):
        attrs['stop'] = stop
    delay = tool_input.get('delay_seconds')
    if delay is None:
        delay = tool_input.get('delaySeconds')
    if isinstance(delay, (int, float)) and not isinstance(delay, bool):
        attrs['delay_seconds'] = delay
    reason = tool_input.get('reason')
    if isinstance(reason, str) and reason:
        attrs['reason'] = reason
    prompt = tool_input.get('prompt')
    if isinstance(prompt, str) and prompt:
        head, dropped = _truncate_output(prompt, 2048)
        attrs['prompt'] = head
        if dropped:
            attrs['prompt_truncated_bytes'] = dropped


def _build_skill_attrs(attrs: dict, tool_input: dict, tool_response: dict, payload: HookPayload) -> None:
    skill_name = tool_input.get('skill')
    if isinstance(skill_name, str) and skill_name:
        attrs['skill_name'] = skill_name
    args = tool_input.get('args')
    if isinstance(args, str) and args:
        head, dropped = _truncate_output(args, _PREVIEW_MAX)
        attrs['skill_args'] = head
        if dropped:
            attrs['skill_args_truncated_bytes'] = dropped


def _build_agent_attrs(attrs: dict, tool_input: dict, tool_response: dict, payload: HookPayload) -> None:
    # The Agent launch carries the subagent's identity (`subagent_type`),
    # its short label (`description`), and the full task `prompt`. None of
    # these reach the SubagentStart event, so the `tool.Agent` span is the
    # only place they can be captured for the trace viewer.
    subagent_type = tool_input.get('subagent_type')
    if isinstance(subagent_type, str) and subagent_type:
        attrs['subagent_type'] = subagent_type
    description = tool_input.get('description')
    if isinstance(description, str) and description:
        attrs['description'] = description
    prompt = tool_input.get('prompt')
    if isinstance(prompt, str) and prompt:
        head, dropped = _truncate_output(prompt, _BASH_STDOUT_MAX)
        attrs['prompt'] = head
        if dropped:
            attrs['prompt_truncated_bytes'] = dropped
    if tool_input.get('run_in_background') is True:
        attrs['run_in_background'] = True


def _build_websearch_input_attrs(attrs: dict, tool_input: dict) -> None:
    """Input-derived WebSearch attr: the search `query` the card displays.
    Split out (input only, no tool_response) so the PreToolUse pending span can
    reuse it — `fullLabel`/`toolLabel`/terminal already read `query`."""
    query = tool_input.get('query')
    if isinstance(query, str) and query:
        attrs['query'] = query


def _build_websearch_attrs(attrs: dict, tool_input: dict, tool_response: dict, payload: HookPayload) -> None:
    _build_websearch_input_attrs(attrs, tool_input)


def _build_webfetch_input_attrs(attrs: dict, tool_input: dict) -> None:
    """Input-derived WebFetch attrs: the `url` (shown on the card) and the
    truncated extraction `fetch_prompt` (detail panel). Input only, so the
    pending span carries the same flat keys the resolved span does."""
    url = tool_input.get('url')
    if isinstance(url, str) and url:
        attrs['url'] = url
    prompt = tool_input.get('prompt')
    if isinstance(prompt, str) and prompt:
        head, dropped = _truncate_output(prompt, _PREVIEW_MAX)
        attrs['fetch_prompt'] = head
        if dropped:
            attrs['fetch_prompt_truncated_bytes'] = dropped


def _build_webfetch_attrs(attrs: dict, tool_input: dict, tool_response: dict, payload: HookPayload) -> None:
    _build_webfetch_input_attrs(attrs, tool_input)


# MCP input/result previews. An MCP call is otherwise opaque in the trace
# (just the tool name), so we capture the params asked and the response
# returned — that's the only way a reader can tell whether, say, a memory
# recall's hits actually relate to the query. Sized to comfortably hold a
# recall round-trip while staying well under the ingest attributes cap.
_MCP_INPUT_MAX = 4 * 1024
_MCP_RESULT_MAX = 16 * 1024


def _json_text(value: object) -> str:
    """Readable JSON for an arbitrary MCP params/response value. Pretty so
    the detail panel's whitespace-pre-wrap renders it legibly; `default=str`
    keeps non-serializable leaves from blowing up the whole dump."""
    try:
        return json.dumps(value, ensure_ascii=False, indent=2, default=str)
    except (TypeError, ValueError):
        return str(value)


def _mcp_result_text(tool_response: object) -> str:
    """Best-effort human-readable string for an MCP `tool_response`.

    MCP results arrive in a few shapes: a bare string, the wire content-block
    envelope (`{'content': [{'type':'text','text':…}]}` or a bare block list),
    or an arbitrary dict (`{'result': …}` from a StructuredOutput tool). Prefer
    concatenated text blocks, then a string `result`, then pretty JSON."""
    if isinstance(tool_response, str):
        return tool_response
    blocks = None
    if isinstance(tool_response, list):
        blocks = tool_response
    elif isinstance(tool_response, dict):
        content = tool_response.get('content')
        if isinstance(content, list):
            blocks = content
        elif isinstance(tool_response.get('result'), str):
            return tool_response['result']
    if blocks is not None:
        texts = [b['text'] for b in blocks
                 if isinstance(b, dict) and isinstance(b.get('text'), str)]
        if texts:
            return '\n'.join(texts)
    return _json_text(tool_response)


def _build_mcp_attrs(attrs: dict, tool: str, tool_input: dict, tool_response: object) -> None:
    # Prefix-dispatched from _emit_span (the builders table is exact-match).
    attrs['mcp'] = True
    if isinstance(tool_input, dict):
        attrs['tool_input_keys'] = list(tool_input.keys())
    # send_to_user is a user-facing message channel: persist the body so
    # the session Messages tab can render it. Truncated to stay well under
    # the ingest attributes-bytes cap. Its prose is shown via the Messages
    # tab, so skip the generic input/result dump for it.
    if tool.endswith('__send_to_user'):
        attrs['user_message'] = str(tool_input.get('message', ''))[:4000]
        return
    # Capture WHAT was asked (params) and WHAT came back (result), truncated,
    # so the span-detail panel can show the round-trip.
    if tool_input:
        head, dropped = _truncate_output(_json_text(tool_input), _MCP_INPUT_MAX)
        attrs['mcp_input'] = head
        if dropped:
            attrs['mcp_input_truncated_bytes'] = dropped
    result_text = _mcp_result_text(tool_response)
    if result_text:
        head, dropped = _truncate_output(result_text, _MCP_RESULT_MAX)
        attrs['mcp_result'] = head
        if dropped:
            attrs['mcp_result_truncated_bytes'] = dropped


_TOOL_BUILDERS: dict = {
    'Agent': _build_agent_attrs,
    'Bash': _build_bash_attrs,
    'Edit': _build_edit_attrs,
    'Write': _build_write_attrs,
    'MultiEdit': _build_multiedit_attrs,
    'Read': _build_read_attrs,
    'Glob': _build_pattern_attrs,
    'Grep': _build_pattern_attrs,
    'AskUserQuestion': _build_ask_attrs,
    'ToolSearch': _build_toolsearch_attrs,
    'TaskCreate': _build_taskcreate_attrs,
    'TaskUpdate': _build_taskupdate_attrs,
    'Workflow': _build_workflow_attrs,
    'TaskOutput': _build_taskoutput_attrs,
    'ScheduleWakeup': _build_schedulewakeup_attrs,
    'Skill': _build_skill_attrs,
    'WebSearch': _build_websearch_attrs,
    'WebFetch': _build_webfetch_attrs,
}


# Builders whose attrs derive purely from `tool_input` (no `tool_response`), so
# a PreToolUse pending span can reuse them to carry the SAME flat keys the
# resolved card reads — otherwise the in-flight card degrades to a bare tool
# name (the conversation labellers ignore the raw `tool_input` dump).
_INPUT_ONLY_BUILDERS: dict = {
    'Bash': _build_bash_input_attrs,
    'WebSearch': _build_websearch_input_attrs,
    'WebFetch': _build_webfetch_input_attrs,
}


def apply_pending_input_attrs(attrs: dict, tool: str, tool_input: dict) -> bool:
    """Populate a pending span's flat input-derived keys for `tool`, mirroring
    the resolved card. Returns True if a builder handled it (the caller then
    skips the raw `tool_input` fallback)."""
    builder = _INPUT_ONLY_BUILDERS.get(tool)
    if builder is None:
        return False
    builder(attrs, tool_input)
    return True


def handle(payload: HookPayload) -> HookResponse | None:
    if not payload.tool_name:
        return None
    # Workflow-tool subagents fire PostToolUse into the launching session, but
    # their activity is captured as the run's own wf_ session — skip to avoid
    # flooding the launching conversation (see HookPayload.is_workflow_subagent).
    if payload.is_workflow_subagent:
        return HookResponse(suppress_output=True)
    try:
        _emit_span(payload)
    except Exception:
        pass
    # No additional_context — the model has the tool input/response already.
    return HookResponse(suppress_output=True)


def _emit_span(payload: HookPayload) -> None:
    from lib.hook_plugin import post_span  # type: ignore
    tool = payload.tool_name or 'unknown'
    tool_input = payload.tool_input or {}
    tool_response = payload.tool_response or {}
    raw = payload.raw or {}

    # Reshape provider-specific result envelopes (Kimi wraps every tool result
    # in `{output, isError}`) onto the Claude-shaped keys the builders read.
    # Claude/Codex pass through unchanged.
    tool_response = payload.resolved_provider.normalize_tool_response(
        tool, tool_input, tool_response
    )

    attrs: dict = {'tool_name': tool}
    # `tool_use_id` is the `toolu_…` Anthropic assigns to this call.
    # Without it the tool-attribution backfill can't match the span to
    # the transcript's tool_use block when it later assigns token costs.
    tu_id = raw.get('tool_use_id')
    if isinstance(tu_id, str) and tu_id:
        attrs['tool_use_id'] = tu_id
    fp = _file_path(tool_input)
    if fp:
        attrs['file_path'] = fp

    builder = _TOOL_BUILDERS.get(tool)
    if builder is not None:
        builder(attrs, tool_input, tool_response, payload)
    elif tool.startswith('mcp__'):
        _build_mcp_attrs(attrs, tool, tool_input, tool_response)

    # If this tool call originated inside a subagent, Claude Code tags the
    # payload with the subagent's `agent_id` (+ optional `agent_type`).
    # Persist both so the trace projection can re-parent the span under
    # the matching `subagent.start` instead of leaving it adrift under
    # the prompt.
    agent_id = raw.get('agent_id')
    if agent_id:
        attrs['agent_id'] = agent_id
        agent_type = raw.get('agent_type')
        if agent_type:
            attrs['agent_type'] = agent_type

    # `prompt_id` (Claude Code 2.1.195+) is the per-submission UUID stamped on
    # every payload, correlating this tool call to the user prompt/turn that
    # spawned it. Stored as `source_prompt_id` — NOT `prompt_id` — because
    # `lib/trace/merge.py` already uses `prompt_id` for an unrelated internal
    # integer ordinal. Only truthy values are kept, so pre-2.1.195 payloads add
    # nothing.
    prompt_id = raw.get('prompt_id')
    if prompt_id:
        attrs['source_prompt_id'] = prompt_id

    # send_to_user lands a durable row in `agent_messages`; pin the span_id
    # up front so that row can deep-link back to this exact tool span.
    is_send = tool.endswith('__send_to_user')
    span_id = uuid.uuid4().hex[:16] if is_send else None

    duration_ms, start_time, end_time = _span_timing(raw)
    post_span(
        trace_id=payload.session_id,
        name=f'tool.{_normalize_tool_name(tool)}',
        attributes=attrs,
        duration_ms=duration_ms,
        start_time=start_time,
        end_time=end_time,
        span_id=span_id,
    )

    if is_send:
        _record_agent_message(payload, tool_input, attrs, span_id)


def _record_agent_message(payload, tool_input: dict, attrs: dict,
                          span_id: str | None) -> None:
    """Persist a send_to_user call into the canonical agent_messages store.

    Best-effort: the hook must never fail because the message store is
    unavailable, so every error is swallowed (the span — with its
    `user_message` attr — still landed regardless).
    """
    is_test = os.environ.get('REGIN_TRACE_TEST', '').lower() in (
        '1', 'true', 'yes')
    try:
        from lib.agent_messages import store
        links = tool_input.get('links')
        store.record_message(
            trace_id=payload.session_id,
            body=str(tool_input.get('message', '')),
            msg_type=tool_input.get('type'),
            title=tool_input.get('title') or None,
            msg_key=tool_input.get('key') or None,
            links=links if isinstance(links, (list, tuple)) else None,
            span_id=span_id,
            agent_id=attrs.get('agent_id'),
            agent_type=attrs.get('agent_type'),
            is_test=is_test,
        )
    except Exception:
        pass
    if tool_input.get('type') == 'lesson':
        _remember_lesson(payload, tool_input, attrs, span_id, is_test)


def _remember_lesson(payload, tool_input: dict, attrs: dict,
                     span_id: str | None, is_test: bool) -> None:
    """`send_to_user(type=lesson)` is the explicit capture endpoint into
    the cross-session agent memory: besides landing in the inbox, the
    lesson body becomes a working-tier memory carrying the span/agent
    provenance this hook already has. Guarded separately from the message
    write so neither store's failure can block the other."""
    try:
        import lib.memory as memory
        if not memory.enabled():
            return
        from lib.memory import retitle
        from lib.memory.scoping import resolve_write_scope
        from lib.memory.store import title_from_body
        body = str(tool_input.get('message', ''))
        # A lesson's title is mandatory; if the agent didn't supply one,
        # derive a placeholder from the body so the lesson is still captured
        # (not silently dropped by the guard below) — and tag it `auto-title`
        # so `regin memory retitle` can later distill a real one-line rule.
        # A truncated body-slice recalls markedly worse than a crafted title.
        supplied_title = (tool_input.get('title') or '').strip()
        tags = ['send_to_user']
        if not supplied_title:
            tags.append(retitle.AUTO_TITLE_TAG)
        common = dict(
            kind='lesson',
            title=supplied_title or title_from_body(body),
            scope=resolve_write_scope(payload.cwd),
            tags=tags,
            source_trace_id=payload.session_id,
            source_span_id=span_id,
            source_agent_id=attrs.get('agent_id'),
            is_test=is_test,
        )
        # `supersedes` (type=lesson only) retires the named memory and chains
        # this one onto it, vs the default fresh insert — the non-destructive
        # correction path that preserves provenance.
        supersedes = str(tool_input.get('supersedes') or '').strip()
        if supersedes and memory.get(supersedes) is not None:
            memory.supersede(supersedes, memory.MemoryInput(body=body, **common))
        else:
            memory.remember(body, **common)
    except Exception:
        pass
