"""Merge individual handler responses into a single HookResponse.

Precedence rules follow the Claude Code hooks spec and our registry contract:

  permission_decision:  deny  > defer > ask > allow
  decision (block):     any "block" wins
  continue_ == False:   any False wins (stop_reason from first to set it)
  suppress_output:      AND across handlers that set it (default True if
                        nothing set, since we don't want to spam stdout)
  additional_context:   joined in priority order with "\n---\n"
  updated_input:        last writer wins; warn if >1 writer
  exit_code:            max across handlers (2 dominates 0)
"""

from __future__ import annotations

import sys
from typing import Iterable

from .core import HookResponse  # noqa: F401


_PERMISSION_RANK = {'allow': 0, 'ask': 1, 'defer': 2, 'deny': 3}


def _merge_last_writer_fields(out: HookResponse, r: HookResponse, counts: dict) -> None:
    """Last-writer-wins fields, plus collision counters for the warnings."""
    if r.session_title is not None:
        out.session_title = r.session_title
    if r.retry is not None:
        out.retry = r.retry
    if r.updated_mcp_tool_output is not None:
        counts['mcp_output'] += 1
        out.updated_mcp_tool_output = r.updated_mcp_tool_output
    if r.updated_input is not None:
        counts['updated_input'] += 1
        out.updated_input = r.updated_input


def _merge_permissions(out: HookResponse, r: HookResponse) -> None:
    """Permission-request (deny-sticky) and ranked permission decisions."""
    if r.permission_request_decision is not None:
        # deny wins over allow per spec: if a later handler allows but
        # an earlier handler denied, keep the deny.
        cur = out.permission_request_decision
        if cur is None or cur.behavior != 'deny':
            out.permission_request_decision = r.permission_request_decision

    if r.permission_decision is not None:
        cur = out.permission_decision
        if cur is None or _PERMISSION_RANK[r.permission_decision] > _PERMISSION_RANK[cur]:
            out.permission_decision = r.permission_decision
            out.permission_reason = r.permission_reason
        elif _PERMISSION_RANK[r.permission_decision] == _PERMISSION_RANK.get(cur, -1):
            if r.permission_reason:
                out.permission_reason = (
                    (out.permission_reason + '\n\n' + r.permission_reason)
                    if out.permission_reason else r.permission_reason
                )


def _merge_block_and_continue(out: HookResponse, r: HookResponse) -> None:
    """Block decision (any wins) and continue_ (any False wins)."""
    if r.decision == 'block':
        out.decision = 'block'
        out.decision_reason = (
            (out.decision_reason + '\n\n' + r.decision_reason)
            if (out.decision_reason and r.decision_reason)
            else (r.decision_reason or out.decision_reason)
        )

    if r.continue_ is False:
        out.continue_ = False
        if out.stop_reason is None:
            out.stop_reason = r.stop_reason


def _merge_output_text(out: HookResponse, r: HookResponse, contexts: list[str]) -> None:
    """suppress_output (AND), system_message (joined), additional_context."""
    if r.suppress_output is not None:
        if out.suppress_output is None:
            out.suppress_output = r.suppress_output
        else:
            out.suppress_output = bool(out.suppress_output and r.suppress_output)

    if r.system_message:
        out.system_message = (
            (out.system_message + '\n' + r.system_message)
            if out.system_message else r.system_message
        )

    if r.additional_context:
        contexts.append(r.additional_context.strip())


def _warn_collisions(counts: dict) -> None:
    """Warn when more than one handler wrote a last-writer-wins field."""
    if counts['updated_input'] > 1:
        sys.stderr.write(
            f'[hook_manager] warning: {counts["updated_input"]} handlers set '
            f'updated_input; last writer kept.\n'
        )
    if counts['mcp_output'] > 1:
        sys.stderr.write(
            f'[hook_manager] warning: {counts["mcp_output"]} handlers set '
            f'updated_mcp_tool_output; last writer kept.\n'
        )


def merge_responses(responses: Iterable[HookResponse]) -> HookResponse:
    """Reduce an ordered iterable of responses to a single response.

    Order matters for `additional_context` concatenation and for which
    handler's `updated_input` is kept when multiple set it (last wins).
    """
    out = HookResponse()
    contexts: list[str] = []
    counts = {'updated_input': 0, 'mcp_output': 0}

    for r in responses:
        if r is None:
            continue
        _merge_last_writer_fields(out, r, counts)
        _merge_permissions(out, r)
        _merge_block_and_continue(out, r)
        _merge_output_text(out, r, contexts)
        out.exit_code = max(out.exit_code, r.exit_code or 0)

    if contexts:
        out.additional_context = '\n---\n'.join(contexts)

    _warn_collisions(counts)

    return out


# Events whose harness response schema does not accept suppressOutput.
# PreToolUse/PostToolUse/PostToolUseFailure have strict permission/block
# shapes; PermissionRequest/PermissionDenied use structured decision objects.
_SUPPRESS_OUTPUT_BLOCKED = frozenset({
    'PreToolUse',
    'PostToolUse',
    'PostToolUseFailure',
    'PermissionRequest',
    'PermissionDenied',
})


def _top_level_fields(event: str, merged: HookResponse) -> dict:
    """Serialize the top-level (non hookSpecificOutput) response fields."""
    out: dict = {}
    if merged.continue_ is not None:
        out['continue'] = merged.continue_
    if merged.stop_reason:
        out['stopReason'] = merged.stop_reason
    if event not in _SUPPRESS_OUTPUT_BLOCKED:
        if merged.suppress_output is not None:
            out['suppressOutput'] = merged.suppress_output
        else:
            out['suppressOutput'] = True
    if merged.system_message:
        out['systemMessage'] = merged.system_message
    if merged.decision == 'block':
        out['decision'] = 'block'
        if merged.decision_reason:
            out['reason'] = merged.decision_reason
    return out


def _permission_request_decision_obj(prd) -> dict:
    """Build the PermissionRequest `decision` nested object."""
    decision_obj: dict = {'behavior': prd.behavior}
    if prd.updated_input is not None:
        decision_obj['updatedInput'] = prd.updated_input
    if prd.updated_permissions is not None:
        decision_obj['updatedPermissions'] = prd.updated_permissions
    if prd.message is not None:
        decision_obj['message'] = prd.message
    if prd.interrupt is not None:
        decision_obj['interrupt'] = prd.interrupt
    return decision_obj


def _event_scoped_specific_fields(event: str, merged: HookResponse, specific: dict) -> bool:
    """Event-scoped hookSpecificOutput fields — only emit when the event
    actually supports them per spec; otherwise we'd send junk. Returns whether
    any field was set."""
    touched = False
    if merged.session_title is not None and event == 'UserPromptSubmit':
        specific['sessionTitle'] = merged.session_title
        touched = True
    if merged.retry is not None and event == 'PermissionDenied':
        specific['retry'] = merged.retry
        touched = True
    if merged.updated_mcp_tool_output is not None and event == 'PostToolUse':
        specific['updatedMCPToolOutput'] = merged.updated_mcp_tool_output
        touched = True

    # PermissionRequest has a uniquely rich output shape: `decision` as a
    # nested object with behavior/updatedInput/updatedPermissions/message/
    # interrupt. Only emit on PermissionRequest per spec.
    prd = merged.permission_request_decision
    if prd is not None and event == 'PermissionRequest':
        specific['decision'] = _permission_request_decision_obj(prd)
        touched = True
    return touched


def _hook_specific_output(event: str, merged: HookResponse) -> tuple[dict, bool]:
    """Build the hookSpecificOutput payload; the bool flags whether any
    event-scoped field was set (i.e. whether to emit the block at all)."""
    specific: dict = {'hookEventName': event}
    touched = False
    if merged.additional_context:
        specific['additionalContext'] = merged.additional_context
        touched = True
    if merged.permission_decision is not None:
        specific['permissionDecision'] = merged.permission_decision
        if merged.permission_reason:
            specific['permissionDecisionReason'] = merged.permission_reason
        touched = True
    if merged.updated_input is not None:
        specific['updatedInput'] = merged.updated_input
        touched = True

    if _event_scoped_specific_fields(event, merged, specific):
        touched = True

    return specific, touched


def response_to_json(event: str, merged: HookResponse) -> dict:
    """Serialize the merged response to the JSON shape Claude Code expects."""
    out = _top_level_fields(event, merged)
    specific, touched = _hook_specific_output(event, merged)
    if touched:
        out['hookSpecificOutput'] = specific
    return out
