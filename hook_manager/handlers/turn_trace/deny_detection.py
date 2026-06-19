"""Sentinel detection + synth-span attribute builders for tool denies / rejects.

`turn_trace` synthesizes `tool.*` spans for two cases where PostToolUse
never fires:

  * **Permission deny** — the user denies a tool at the permission prompt
    (or picks "Chat about this" on AskUserQuestion). The tool body never
    runs; the transcript carries a paired `tool_result` with `is_error=true`
    and one of two sentinel phrases in its content.
  * **Pre-execution tool_use_error** — Claude Code rejects the call
    (Read-before-Write, missing tool implementation, malformed input).
    The body never runs; the transcript wraps the rejection in a
    `<tool_use_error>…</tool_use_error>` envelope.

These two states are distinct from PostToolUseFailure (runtime failures —
the body ran and threw) and travel through their own span lineage:
`tooldeny-*` / `askdeny-*` for denies, `toolerr-*` for tool_use_errors.

If Anthropic rewords the deny phrases, the sentinel misses and the
synth never happens — verify against current Claude Code if adding a
third variant.
"""

from __future__ import annotations

import json
import re

# Claude Code's permission-deny tool_result text. The first phrase
# covers any tool the user denies at the permission prompt; the second
# is the AskUserQuestion-only "Chat about this" variant where the user
# wants to clarify rather than reject outright. The data on the span
# is correct either way — `deny_kind` lets the UI label them
# differently.
_HARD_DENY_PHRASE = "doesn't want to proceed with this tool use"
_CHAT_DENY_PHRASE = 'wants to clarify these questions'
_DENIAL_REASON_MAX_BYTES = 4096

# Claude Code wraps pre-execution tool rejections (Read-before-Write,
# missing tool implementations, malformed inputs) in a
# `<tool_use_error>…</tool_use_error>` envelope. The tool body never
# runs, so neither PostToolUse nor PostToolUseFailure fires — leaving
# `post_tool_trace` with no `tool.<name>` row to mint.
_TOOL_USE_ERROR_OPEN = '<tool_use_error>'
_TOOL_USE_ERROR_CLOSE = '</tool_use_error>'
_TOOL_INPUT_PATH_RE = re.compile(
    r'"(?:file_path|path|notebook_path)"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"'
)


def _is_permission_deny(result_text: object) -> bool:
    """True when a tool_result's content text matches one of the
    permission-deny sentinels. Used to gate synth-span creation so
    ordinary tool failures (Bash exit codes, EISDIR) — which already
    get a `tool.failure` span via PostToolUseFailure — don't get a
    second, conflicting synth span."""
    if not isinstance(result_text, str) or not result_text:
        return False
    return _HARD_DENY_PHRASE in result_text or _CHAT_DENY_PHRASE in result_text


def _is_tool_use_error(result_text: object) -> bool:
    """True when a tool_result's content is wrapped in the
    `<tool_use_error>` envelope Claude Code emits for pre-execution
    rejections. Distinct from `_is_permission_deny` (a user choice) and
    from raw runtime failures (which travel through PostToolUseFailure
    as `tool.failure`)."""
    if not isinstance(result_text, str) or not result_text:
        return False
    return _TOOL_USE_ERROR_OPEN in result_text


def _strip_tool_use_error_envelope(text: str) -> str:
    start = text.find(_TOOL_USE_ERROR_OPEN)
    if start < 0:
        return text
    inner_start = start + len(_TOOL_USE_ERROR_OPEN)
    end = text.find(_TOOL_USE_ERROR_CLOSE, inner_start)
    if end < 0:
        return text[inner_start:].strip()
    return text[inner_start:end].strip()


def _direct_input_path(tool_input: dict) -> str | None:
    """Pull file_path / path / notebook_path off a tool_input dict."""
    direct = (
        tool_input.get('file_path')
        or tool_input.get('path')
        or tool_input.get('notebook_path')
    )
    return direct if isinstance(direct, str) and direct else None


def _parse_preview_path(preview: str) -> str | None:
    """Recover a file path from the `{__truncated, preview}` shim that
    transcript_usage carries when the captured tool_input was too big
    to copy whole. Tries json.loads first; falls back to a regex over
    the JSON-encoded preview string."""
    try:
        parsed = json.loads(preview)
    except (TypeError, ValueError):
        m = _TOOL_INPUT_PATH_RE.search(preview)
        if not m:
            return None
        try:
            return json.loads(f'"{m.group(1)}"')
        except (TypeError, ValueError):
            return m.group(1)
    if not isinstance(parsed, dict):
        return None
    nested = _direct_input_path(parsed)
    return nested


def _tool_input_file_path(tool_input: object) -> str | None:
    """Best-effort file path extractor for captured tool input.

    Normal live spans usually carry the raw tool_input dict, but denied /
    rejected synth spans may only have the capped `{__truncated, preview}`
    shim from transcript_usage. Parse that preview when needed so the UI
    and historical backfills can still show which file the tool targeted.
    """
    if not isinstance(tool_input, dict):
        return None
    direct = _direct_input_path(tool_input)
    if direct:
        return direct
    preview = tool_input.get('preview')
    if isinstance(preview, str) and preview:
        return _parse_preview_path(preview)
    return None


def _ask_question_attrs(qs: list) -> list[dict]:
    """Structure AskUserQuestion questions for the synth deny span so
    the existing per-question renderer works unchanged."""
    from ..post_tool_trace import _ask_option
    out: list[dict] = []
    for q in qs:
        if not isinstance(q, dict):
            continue
        out.append({
            'question': q.get('question'),
            'header': q.get('header'),
            'options': [
                _ask_option(o) for o in (q.get('options') or [])
                if isinstance(o, dict)
            ],
            'multiSelect': q.get('multiSelect', False),
        })
    return out


def _apply_denial_reason(attrs: dict, result_text: object) -> None:
    if not isinstance(result_text, str) or not result_text:
        return
    capped = result_text[:_DENIAL_REASON_MAX_BYTES]
    attrs['denial_reason'] = capped
    if len(result_text) > len(capped):
        attrs['denial_reason_truncated_bytes'] = len(result_text) - len(capped)
    attrs['deny_kind'] = 'chat' if _CHAT_DENY_PHRASE in result_text else 'deny'


def _apply_tool_input(attrs: dict, tc: dict) -> None:
    tool_input = tc.get('tool_input')
    file_path = _tool_input_file_path(tool_input)
    if file_path:
        attrs['file_path'] = file_path
    if tool_input is not None:
        attrs['tool_input'] = tool_input
        dropped = tc.get('tool_input_truncated_bytes')
        if dropped:
            attrs['tool_input_truncated_bytes'] = dropped


def _deny_skeleton(tool_name: str, tu_id: str) -> dict:
    """The `tool.<name>` deny-span attrs shared by every deny path: a denied
    flag and the ids that link back to the originating call. Both the Claude
    transcript-sentinel path (`_build_deny_attrs`) and the provider-recorded
    path (`build_recorded_deny_attrs`) build on this so the deny contract lives
    in one place."""
    return {'tool_name': tool_name, 'tool_use_id': tu_id, 'denied': True}


# Recorded-denial command preview cap (Bash). Kept short: the full command
# still rides along in `tool_input`.
_DENY_COMMAND_PREVIEW_MAX = 200


def build_recorded_deny_attrs(
    tool_name: str,
    tu_id: str,
    denial_reason: object,
    tool_input: object,
) -> dict:
    """Deny-span attrs for a provider that *records* permission denials in its
    own transcript (Kimi) rather than through Claude's tool_result sentinel.

    Produces the same `denied=True` `tool.<name>` shape `_build_deny_attrs`
    does, so the UI renders both identically. Lives beside it (rather than in
    the span poster) so a change to the deny contract touches one module.

    For Bash we additionally set `command_preview` (not a top-level `command`):
    fullLabel renders `Bash: <preview>` in the InlineToolRow that owns the
    deny styling — a top-level `command` would divert rendering to BashCard,
    whose badge keys on `interrupted` not `denied`. The full command stays in
    `tool_input`.
    """
    attrs = _deny_skeleton(tool_name, tu_id)
    attrs['deny_kind'] = 'deny'
    if isinstance(denial_reason, str) and denial_reason:
        attrs['denial_reason'] = denial_reason[:_DENIAL_REASON_MAX_BYTES]
    if isinstance(tool_input, dict) and tool_input:
        attrs['tool_input'] = tool_input
        cmd = tool_input.get('command')
        if tool_name == 'Bash' and isinstance(cmd, str) and cmd:
            attrs['command_preview'] = (
                cmd[:_DENY_COMMAND_PREVIEW_MAX]
                + ('…' if len(cmd) > _DENY_COMMAND_PREVIEW_MAX else '')
            )
    return attrs


def _build_deny_attrs(
    tool_name: str,
    tu_id: str,
    turn_uuid: str | None,
    tc: dict,
    result_text: object,
) -> dict:
    """Assemble the attributes dict for a synthesized deny span.

    Carries:
      * `tool_name`, `tool_use_id`, `turn_uuid` — link back to the
        originating call.
      * `denied=True`, `deny_kind` — flag the variant.
      * `denial_reason` — the user-visible message Claude Code injected
        (capped).
      * `tool_input` — the raw `tool_use.input` block (cap honoured
        upstream in `_capture_tool_input`).
      * `questions` — structured AskUserQuestion form (only when the
        denied tool is AskUserQuestion), so the existing per-question
        renderer keeps working without a fallback path.
    """
    attrs = _deny_skeleton(tool_name, tu_id)
    if turn_uuid:
        attrs['turn_uuid'] = turn_uuid
    _apply_denial_reason(attrs, result_text)
    _apply_tool_input(attrs, tc)
    if tool_name == 'AskUserQuestion':
        qs = tc.get('input_questions')
        if isinstance(qs, list) and qs:
            attrs['questions'] = _ask_question_attrs(qs)
    return attrs


def _build_tool_use_error_attrs(
    tool_name: str,
    tu_id: str,
    turn_uuid: str | None,
    tc: dict,
    result_text: object,
) -> dict:
    """Mirror of `_build_deny_attrs` for pre-execution tool rejections.
    Surfaces the inner reason (envelope stripped) so the trace UI can
    show "Read-before-Write" rather than the raw `<tool_use_error>` tag."""
    attrs: dict = {
        'tool_name': tool_name,
        'tool_use_id': tu_id,
        'rejected': True,
        'reject_kind': 'tool_use_error',
    }
    if turn_uuid:
        attrs['turn_uuid'] = turn_uuid
    if isinstance(result_text, str) and result_text:
        inner = _strip_tool_use_error_envelope(result_text)
        capped = inner[:_DENIAL_REASON_MAX_BYTES]
        attrs['reject_reason'] = capped
        if len(inner) > len(capped):
            attrs['reject_reason_truncated_bytes'] = len(inner) - len(capped)
    _apply_tool_input(attrs, tc)
    return attrs
