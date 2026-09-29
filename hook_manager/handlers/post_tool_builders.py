"""Per-tool span-attribute builders for PostToolUse tool spans, keyed by tool
name in `_TOOL_BUILDERS`."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta

from ..core import HookPayload
from .post_tool_diffs import (
    _BASH_COMMAND_MAX,
    _BASH_STDERR_MAX,
    _BASH_STDOUT_MAX,
    _PREVIEW_MAX,
    _READ_CONTENT_MAX,
    _attach_bash_edit_diff,
    _attach_edit_diff,
    _attach_edit_metadata,
    _bash_preview,
    _compute_unified_diff,
    _truncate_output,
)


# Providers whose tool paths are relative to the session cwd and are anchored
# here rather than in every consumer. Claude is deliberately absent: it emits
# relative paths too (13,109 of 64,259 stored file_path spans, mostly `Read`),
# so absolutizing there would file new spans under a different identity than
# every span already recorded and split a file's history in two.
_CWD_RELATIVE_PATH_PROVIDERS = frozenset({'kimi'})


def _file_path(tool_input: dict, cwd: str | None = None) -> str | None:
    ti = tool_input or {}
    fp = ti.get('file_path') or ti.get('path') or ti.get('notebook_path')
    if isinstance(fp, str) and fp and cwd and not os.path.isabs(fp):
        return os.path.normpath(os.path.join(cwd, fp))
    return fp


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
    description = tool_input.get('description')
    if isinstance(description, str) and description:
        attrs['command_description'] = description[:_PREVIEW_MAX]
    # The DECLARED intent, which is all a PreToolUse row can know and all that
    # survives when the PostToolUse that would have carried `background_task_id`
    # is lost. The harness may also background a call the model ran in the
    # foreground; that one is only ever visible through the response id.
    if tool_input.get('run_in_background') is True:
        attrs['run_in_background'] = True


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
    # Guarded separately from the handler's own catch-all, which would drop
    # the whole span: a trace row must never be lost to a rendering nicety.
    try:
        _attach_bash_edit_diff(attrs, tool_response)
    except Exception:
        pass
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


def _set_str(attrs: dict, key: str, value: object) -> None:
    if isinstance(value, str) and value:
        attrs[key] = value


def _set_truncated_str(attrs: dict, key: str, value: object, limit: int) -> None:
    """Store a non-empty string under `key`, capped at `limit`, recording the
    dropped byte count under `<key>_truncated_bytes` when it was cut."""
    if not (isinstance(value, str) and value):
        return
    head, dropped = _truncate_output(value, limit)
    attrs[key] = head
    if dropped:
        attrs[f'{key}_truncated_bytes'] = dropped


def _build_ask_attrs(attrs: dict, tool_input: dict, tool_response: dict, payload: HookPayload) -> None:
    questions = tool_input.get('questions') or []
    attrs['questions'] = [_ask_question(q) for q in questions]
    for key in ('answers', 'annotations'):
        value = tool_response.get(key) or tool_input.get(key)
        if value:
            attrs[key] = value


def _ask_question(q: dict) -> dict:
    return {
        'question': q.get('question'),
        'header': q.get('header'),
        'options': [_ask_option(o) for o in (q.get('options') or [])],
        'multiSelect': q.get('multiSelect', False),
    }


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
        names = _selected_tool_names(q)
        if names:
            attrs['selected_tools'] = names
    _set_int(attrs, 'max_results', tool_input.get('max_results'))
    m = tool_response.get('matches')
    if isinstance(m, list) and m:
        attrs['loaded_tools'] = [str(x) for x in m if isinstance(x, str)]
    _set_int(attrs, 'total_deferred_tools', tool_response.get('total_deferred_tools'))


def _selected_tool_names(query: str) -> list[str]:
    if not query.startswith('select:'):
        return []
    return [n.strip() for n in query[len('select:'):].split(',') if n.strip()]


def _set_int(attrs: dict, key: str, value: object) -> None:
    if isinstance(value, int):
        attrs[key] = value


def _build_taskcreate_attrs(attrs: dict, tool_input: dict, tool_response: dict, payload: HookPayload) -> None:
    _set_str(attrs, 'subject', tool_input.get('subject'))
    _set_truncated_str(attrs, 'description', tool_input.get('description'), _BASH_STDOUT_MAX)
    _set_str(attrs, 'active_form', tool_input.get('activeForm'))
    task_id = _created_task_id(tool_response)
    if task_id is not None:
        attrs['task_id'] = str(task_id)


def _created_task_id(tool_response: dict):
    # Claude Code returns the new task_id inside `tool_response.task.id`
    # (the dedicated TaskCreate hook fixture); the older flat shape
    # `tool_response.task_id` is unlikely but handled as a fallback.
    task = tool_response.get('task') if isinstance(tool_response.get('task'), dict) else None
    return (task.get('id') if task else None) or tool_response.get('task_id') or tool_response.get('taskId')


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


# Snapshot task-list statuses → the vocabulary the task-list surfaces render.
# Kimi's `TodoList` says `done` where those surfaces say `completed`. Anything
# unrecognised degrades to `pending` rather than leaking a status the UI has no
# chip for.
_TODO_STATUS_MAP = {
    'pending': 'pending',
    'not_started': 'pending',
    'todo': 'pending',
    'in_progress': 'in_progress',
    'active': 'in_progress',
    'done': 'completed',
    'complete': 'completed',
    'completed': 'completed',
    'cancelled': 'deleted',
    'canceled': 'deleted',
    'deleted': 'deleted',
}


def _todo_status(value: object) -> str:
    if not isinstance(value, str):
        return 'pending'
    return _TODO_STATUS_MAP.get(value.strip().lower(), 'pending')


def _todo_subject(todo: dict) -> str:
    # Kimi names the field `title`; `content`/`subject` cover the other
    # snapshot spellings seen in the wild.
    for key in ('title', 'content', 'subject'):
        v = todo.get(key)
        if isinstance(v, str) and v:
            return v
    return ''


def _todo_entry(index: int, todo: object) -> dict | None:
    if not isinstance(todo, dict):
        return None
    subject = _todo_subject(todo)
    if not subject:
        return None
    entry = {
        'task_id': str(index),
        'subject': subject,
        'status': _todo_status(todo.get('status')),
    }
    active_form = todo.get('activeForm') or todo.get('active_form')
    if isinstance(active_form, str) and active_form:
        entry['active_form'] = active_form
    return entry


def _build_todolist_attrs(attrs: dict, tool_input: dict, tool_response: dict, payload: HookPayload) -> None:
    """Kimi's `TodoList` resends the WHOLE list on every write, where
    TaskCreate/TaskUpdate each carry a single task. Store the snapshot under
    `todos` in the same per-task shape those builders emit
    (`task_id`/`subject`/`status`) so the session task-list fold can replay
    either kind. `task_id` is the 1-based position: the payload carries no id of
    its own.

    Claude Code's own `TodoWrite` is deliberately NOT routed here — see
    `_TOOL_BUILDERS`.
    """
    todos = tool_input.get('todos')
    if not isinstance(todos, list):
        return
    entries = [e for e in (_todo_entry(i, t) for i, t in enumerate(todos, 1))
               if e is not None]
    if entries:
        attrs['todos'] = entries


def _build_taskoutput_attrs(attrs: dict, tool_input: dict, tool_response: dict, payload: HookPayload) -> None:
    # `retrieval_status` ('success' / 'not_found' / 'timed_out') is
    # distinct from the wrapped task's `status` and matters for
    # block=false polls. The span's own status_code stays OK even when
    # the wrapped task exited non-zero — this tool call succeeded.
    task_id = tool_input.get('task_id') or tool_input.get('taskId')
    if task_id is not None:
        attrs['task_id'] = str(task_id)
    _set_str(attrs, 'retrieval_status', tool_response.get('retrieval_status'))
    task = tool_response.get('task') if isinstance(tool_response.get('task'), dict) else None
    if task:
        _attach_wrapped_task(attrs, task)


def _attach_wrapped_task(attrs: dict, task: dict) -> None:
    if 'task_id' not in attrs and task.get('task_id') is not None:
        attrs['task_id'] = str(task['task_id'])
    for field in ('task_type', 'status', 'description'):
        _set_str(attrs, field, task.get(field))
    exit_code = task.get('exit_code')
    if isinstance(exit_code, int):
        attrs['exit_code'] = exit_code
    _set_truncated_str(attrs, 'output', task.get('output'), _BASH_STDOUT_MAX)


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
    blocks = _mcp_content_blocks(tool_response)
    if blocks is None and isinstance(tool_response, dict) \
            and isinstance(tool_response.get('result'), str):
        return tool_response['result']
    texts = _block_texts(blocks or [])
    return '\n'.join(texts) if texts else _json_text(tool_response)


def _block_texts(blocks: list) -> list[str]:
    return [b['text'] for b in blocks
            if isinstance(b, dict) and isinstance(b.get('text'), str)]


def _mcp_content_blocks(tool_response: object) -> list | None:
    if isinstance(tool_response, list):
        return tool_response
    if isinstance(tool_response, dict) and isinstance(tool_response.get('content'), list):
        return tool_response['content']
    return None


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
    # Claude Code's own `TodoWrite` is deliberately absent: its main agent and
    # each of its subagents write independent lists into the SAME trace_id, so
    # folding them as one snapshot stream (web/blueprints/trace/sessions.py)
    # makes every subagent write retire the main agent's live tasks and vice
    # versa. Claude's task list reaches the trace via TaskCreate/TaskUpdate.
    'TodoList': _build_todolist_attrs,
    'Workflow': _build_workflow_attrs,
    'TaskOutput': _build_taskoutput_attrs,
    'ScheduleWakeup': _build_schedulewakeup_attrs,
    'Skill': _build_skill_attrs,
    'WebSearch': _build_websearch_attrs,
    'WebFetch': _build_webfetch_attrs,
    # Kimi's URL tool is named FetchURL but ships WebFetch's `url`/`prompt`.
    'FetchURL': _build_webfetch_attrs,
}


# Builders whose attrs derive purely from `tool_input` (no `tool_response`), so
# a PreToolUse pending span can reuse them to carry the SAME flat keys the
# resolved card reads — otherwise the in-flight card degrades to a bare tool
# name (the conversation labellers ignore the raw `tool_input` dump).
_INPUT_ONLY_BUILDERS: dict = {
    'Bash': _build_bash_input_attrs,
    'WebSearch': _build_websearch_input_attrs,
    'WebFetch': _build_webfetch_input_attrs,
    'FetchURL': _build_webfetch_input_attrs,
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
