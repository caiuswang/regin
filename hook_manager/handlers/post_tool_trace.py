"""Handler: PostToolUse → emit a `tool.{name}` span per call.

The model already sees tool input/response in its transcript, so this
handler does NOT return `additional_context` — that was transcript noise
(the `fa3922e` silent-trace policy). But the *trace DB* still needs the
data so the session-trace view can show tool activity under each prompt
span. This handler posts one span per tool call; full raw payloads stay
in `~/.claude/hook-payloads.jsonl` via `trace_payload`.
"""

from __future__ import annotations

import os
import uuid

from ..core import HookPayload, HookResponse
from .post_tool_builders import (
    _CWD_RELATIVE_PATH_PROVIDERS,
    _MCP_RESULT_MAX,
    _TOOL_BUILDERS,
    _ask_option,
    _build_agent_attrs,
    _build_ask_attrs,
    _build_mcp_attrs,
    _build_workflow_attrs,
    _file_path,
    _mcp_result_text,
    _normalize_tool_name,
    _set_str,
    _span_timing,
    apply_pending_input_attrs,
)
from .post_tool_diffs import (
    _BASH_COMMAND_MAX,
    _BASH_EDIT_DIFF_MAX,
    _BASH_STDERR_MAX,
    _BASH_STDOUT_MAX,
    _EDIT_DIFF_MAX,
    _PREVIEW_MAX,
    _READ_CONTENT_MAX,
    _bash_preview,
    _truncate_output,
)

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

    provider = payload.resolved_provider
    # Reshape provider-specific result envelopes (Kimi wraps every tool result
    # in `{output, isError}`) onto the Claude-shaped keys the builders read.
    # Claude/Codex pass through unchanged.
    tool_response = provider.normalize_tool_response(
        tool, tool_input, tool_response
    )

    attrs = _call_identity_attrs(payload, provider, tool, tool_input, raw)
    builder = _TOOL_BUILDERS.get(tool)
    if builder is not None:
        builder(attrs, tool_input, tool_response, payload)
    elif tool.startswith('mcp__'):
        _build_mcp_attrs(attrs, tool, tool_input, tool_response)
    _attach_origin_attrs(attrs, raw)

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


def _call_identity_attrs(payload: HookPayload, provider, tool: str,
                         tool_input: dict, raw: dict) -> dict:
    attrs: dict = {'tool_name': tool}
    # `tool_use_id` is the `toolu_…` Anthropic assigns to this call.
    # Without it the tool-attribution backfill can't match the span to
    # the transcript's tool_use block when it later assigns token costs.
    _set_str(attrs, 'tool_use_id', raw.get('tool_use_id'))
    anchor = (payload.cwd
              if provider.provider_id in _CWD_RELATIVE_PATH_PROVIDERS else None)
    fp = _file_path(tool_input, anchor)
    if fp:
        attrs['file_path'] = fp
    return attrs


def _attach_origin_attrs(attrs: dict, raw: dict) -> None:
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
        # The exported tree is the agent's navigation surface, so a lesson
        # captured mid-session has to land there before the next prompt reads
        # it. Stamp-guarded, so this is one aggregate query when nothing moved.
        from lib.memory.tree_io import export_tree_if_stale
        from lib.settings import settings as _settings
        export_tree_if_stale(str(_settings.main_worktree))
    except Exception:
        pass
