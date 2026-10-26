"""Entry-level parsing helpers and the per-turn builder behind the transcript scan."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from lib.tokens.token_estimator import (
    estimate_text_tokens,
    estimate_tool_use_tokens,
)
from lib.trace.transcript_models import TurnUsage, has_encrypted_thinking
from lib.trace.peer_message import is_non_human_origin
from lib.trace.prompt_images_resolve import extract_image_parts
from lib.trace.transcript_parsers import (
    _delta_ms,
    _extract_text_blocks,
    _normalize_dict_keys,
    _truncate_utf8,
    is_interrupt_marker,
)


# Local slash-command markers. Claude Code wraps every standalone local
# command in three transcript entries (caveat + name + stdout — the
# caveat is omitted for system-emitted ones like /usage). The entry
# type is `type=user` for user-typed commands and `type=system,
# subtype=local_command` for system-emitted ones, so detection runs on
# both with substring presence as the gate.
_COMMAND_NAME_RE = re.compile(r'<command-name>([^<]+)</command-name>')


_COMMAND_ARGS_RE = re.compile(r'<command-args>([^<]*)</command-args>')


_LOCAL_COMMAND_STDOUT_RE = re.compile(
    r'<local-command-stdout>(.*?)</local-command-stdout>', re.DOTALL
)


_LOCAL_COMMAND_CAVEAT_TAG = '<local-command-caveat>'

# Bang/bash commands (`!ls`) are a second local-command shape: a
# `<bash-input>` entry plus a paired entry carrying both
# `<bash-stdout>` and `<bash-stderr>`. Like slash commands they never
# fire UserPromptSubmit, so the transcript scan is the only way to
# surface them.
_BASH_INPUT_RE = re.compile(r'<bash-input>(.*?)</bash-input>', re.DOTALL)


_BASH_STDOUT_RE = re.compile(r'<bash-stdout>(.*?)</bash-stdout>', re.DOTALL)


_BASH_STDERR_RE = re.compile(r'<bash-stderr>(.*?)</bash-stderr>', re.DOTALL)

# Cap for serialized `tool_use.input` carried on synthesized deny
# spans. 16 KB matches the skill-listing cap convention and is enough
# to surface a Bash command, Edit diff, or browser_evaluate JS snippet
# without bloating the span attributes blob.
_DENY_INPUT_MAX_BYTES = 16 * 1024

# Attachment kinds worth tracing. Everything else (notably
# `hook_success` and `hook_additional_context`) is harness noise that
# would drown the timeline.
#
# `queued_command` is special: when the user types a prompt while the
# agent is mid-turn, Claude Code queues it and, on dequeue, injects it
# as this attachment instead of firing UserPromptSubmit. So it leaves
# no `prompt` span behind (same blind spot as local commands) — the
# transcript scan is the only way to recover it. See
# `prompt_anchor_spans._post_queued_command_span`.
_TRACED_ATTACHMENT_KINDS: frozenset[str] = frozenset({
    'task_reminder',
    'skill_listing',
    'deferred_tools_delta',
    'queued_command',
})

# System-event subtypes worth tracing. Currently:
#   * turn_duration     — Claude Code's own wall-clock per turn
#                         (includes hooks); the canonical "how long
#                         did this turn take" metric.
#   * stop_hook_summary — per-hook latency on the Stop event, plus
#                         hookCount/hookErrors. Useful for spotting
#                         a slow hook that's adding seconds to every
#                         turn.
#   * away_summary      — the "recap" Claude Code writes when the
#                         session goes idle: a prose summary of what
#                         the turn was doing, in the top-level
#                         `content` string. Surfaced as a span so the
#                         trace shows the recap inline.
#   * model_refusal_fallback — the CLI's own record that a response was
#                         refused and silently retried on a fallback
#                         model (originalModel/fallbackModel/
#                         apiRefusalCategory/retractedMessageUuids).
#                         Observability only: surfaced as a marker span so
#                         a resolved-model / token-shape discontinuity has
#                         an explanation instead of appearing unexplained.
_TRACED_SYSTEM_SUBTYPES: frozenset[str] = frozenset({
    'turn_duration',
    'stop_hook_summary',
    'away_summary',
    'model_refusal_fallback',
})


@dataclass
class _TurnBuilder:
    """Mutable per-turn accumulator used during the streaming pass.

    A single API response can be split across multiple `assistant`
    entries in the transcript (text-block in one entry, tool_use-block
    in the next, sharing `message.id`). The first entry per dedup key
    owns the usage counters; subsequent entries contribute extra text
    fragments. We build a frozen `TurnUsage` from the accumulator at
    the end of the pass.
    """

    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_creation_1h_tokens: int = 0
    uuid: str | None = None
    timestamp: str | None = None
    request_id: str | None = None
    message_id: str | None = None
    parent_uuid: str | None = None
    prompt_uuid: str | None = None
    text_parts: list[str] = field(default_factory=list)
    # Thinking-block fragments (visible to the local transcript only
    # when extended thinking text isn't redacted). Tracked separately
    # from `text_parts` so the final `text` field stays "what the user
    # saw"; `thinking_text` carries the reasoning.
    thinking_parts: list[str] = field(default_factory=list)
    thinking_blocks: int = 0
    thinking_signature_bytes: int = 0
    # Patched in when the linear scan reaches a `system: turn_duration`
    # entry whose chain points back to this turn's assistant uuid.
    # Represents the FULL prompt cycle (every API call + tools +
    # hooks), so on a multi-iteration turn it lands only on the
    # final text-emitting assistant_response.
    turn_total_duration_ms: int | None = None
    # Timestamp of the entry that immediately preceded this turn's
    # first content block — used to derive the per-API-call latency
    # (current entry's timestamp − prior entry's timestamp). The
    # transcript doesn't carry "API call started" directly; the prior
    # entry's flush time is the closest proxy available locally.
    prior_entry_timestamp: str | None = None
    # Per-turn tool-call summary, populated as `tool_use` blocks are
    # seen and patched in-place when the matching `tool_result` block
    # arrives later in the linear scan. Each dict:
    #   {id, name, is_error, output_token_estimate, input_token_estimate,
    #    image_token_estimate}
    # `is_error` stays None until a tool_result is observed (in-flight);
    # we never coerce to False at finalize so consumers can distinguish.
    # `input_token_estimate` is None until the matching tool_result is
    # observed; on results that are *images only*, `image_token_estimate`
    # equals `input_token_estimate` so consumers can split text vs image.
    # A `Skill` call also accumulates its injected body here — see
    # `_patch_skill_payload`.
    tool_calls: list[dict] = field(default_factory=list)
    # Set from the entry's `isApiErrorMessage`. The one shape that
    # survives `_counted_builders` with an all-zero usage block.
    is_api_error: bool = False


def _local_command_text(entry_n: dict, etype: str | None) -> str | None:
    """Return the raw text body where local-command tags would live, or
    None if this entry isn't a candidate. Handles both shapes:

      * `type=user` — body lives in `message.content` (string).
      * `type=system, subtype=local_command` — body lives in the
        top-level `content` (string).

    Non-string content (lists of blocks for tool_result-bearing user
    entries) returns None — those aren't local-command rows.
    """
    if etype == 'system':
        if entry_n.get('subtype') != 'local_command':
            return None
        content = entry_n.get('content')
        return content if isinstance(content, str) else None
    if etype == 'user':
        msg = _normalize_dict_keys(entry_n.get('message'))
        content = msg.get('content')
        return content if isinstance(content, str) else None
    return None


def _capture_tool_input(raw: object) -> tuple[object | None, int]:
    """Serialize-then-cap a tool_use input for the synth-deny path.

    Returns `(captured, dropped_bytes)`. `captured` is the original
    dict when it fits, a `{__truncated: true, preview: <str>}` shim
    when it doesn't, or None if json.dumps fails entirely. The shim
    keeps the attribute self-describing — the frontend can detect it
    without a separate `*_truncated` companion field — while preserving
    a human-readable head of the raw JSON.
    """
    try:
        serialized = json.dumps(raw, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return None, 0
    encoded = serialized.encode('utf-8')
    if len(encoded) <= _DENY_INPUT_MAX_BYTES:
        return raw, 0
    preview = encoded[:_DENY_INPUT_MAX_BYTES].decode('utf-8', errors='ignore')
    return {'__truncated': True, 'preview': preview}, len(encoded) - _DENY_INPUT_MAX_BYTES


def _maybe_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _entry_types(entry_n: dict) -> tuple[str | None, str | None]:
    """Lowercase (type, role) for an entry; either may be None."""
    raw_type = entry_n.get('type')
    raw_role = entry_n.get('role')
    etype = raw_type.lower() if isinstance(raw_type, str) else None
    role = raw_role.lower() if isinstance(raw_role, str) else None
    return etype, role


def _first_block_is_tool_result(content: object) -> bool:
    if not isinstance(content, list) or not content:
        return False
    first = content[0]
    return isinstance(first, dict) and first.get('type') == 'tool_result'


def _user_prompt_text(content: object) -> str | None:
    """Submitted text of a user-prompt entry. Plain prompts carry a bare
    string; slash-command expansions and image prompts carry a list of
    content blocks. Returns None when no text could be extracted."""
    if isinstance(content, str):
        return content or None
    blocks = _extract_text_blocks(content)
    return '\n'.join(blocks) if blocks else None


def queued_prompt_content(att_payload: dict) -> tuple[str | None, list[dict]]:
    """Text + inline base64 image parts of a prompt-mode `queued_command`
    attachment payload (normalized keys).

    `prompt` is a bare string for a plain queued prompt, but a list of
    content blocks (text + image) when the queued prompt carried pasted
    images — same dual shape as a user entry's `message.content`. A
    non-prompt `command_mode` (queued slash command) returns `(None, [])`:
    it is not a model prompt and never anchors a turn. Neither does a
    prompt-mode entry the harness produced (a subagent hand-back arrives as
    `origin.kind == 'peer'`): the user never typed it."""
    mode = att_payload.get('command_mode')
    if mode is not None and mode != 'prompt':
        return None, []
    if is_non_human_origin(att_payload.get('origin')):
        return None, []
    prompt = att_payload.get('prompt')
    return _user_prompt_text(prompt), extract_image_parts(prompt)


# Claude Code emits a second synthetic user entry alongside an image prompt
# whose text is purely `[Image: source: <path>]` lines (one per pasted image).
_IMAGE_REF_LINE_RE = re.compile(r'^\[Image: source: .+\]$')

# Substrings that mark a `type:user` entry as a local-command echo
# (/compact, /clear, !ls, their stdout, and the typed-command caveat)
# rather than a real typed prompt. Local commands get their own
# `harness.local_command` (`cmd-<uuid>`) span, never a `prompt-` anchor,
# so a turn must never resolve its prompt anchor to one of these.
_LOCAL_COMMAND_MARKERS: tuple[str, ...] = (
    '<command-name>',
    '<bash-input>',
    '<bash-stdout>',
    '<local-command-stdout>',
    _LOCAL_COMMAND_CAVEAT_TAG,
)


def _is_image_reference_carrier(text: str) -> bool:
    """True when every non-blank line is an `[Image: source: …]` reference —
    i.e. a synthetic image carrier, not a real typed prompt."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return bool(lines) and all(_IMAGE_REF_LINE_RE.match(ln) for ln in lines)


def _is_excluded_prompt_text(text: str) -> bool:
    """Text-level exclusions from the real-prompt set: synthetic image
    carriers, background `<task-notification>` completions, interrupt
    markers, and local-command echoes are never real typed prompts that
    anchor a turn.

    The interrupt marker is the user pressing Esc, never a turn opener —
    left in, it becomes a phantom `PROMPT · TURN n` card that swallows the
    work of whatever the user typed next (a whole 30-minute turn, filed
    under "[Request interrupted by user]")."""
    if text.lstrip().startswith('<task-notification>'):
        return True
    if is_interrupt_marker(text):
        return True
    if _is_image_reference_carrier(text):
        return True
    return any(marker in text for marker in _LOCAL_COMMAND_MARKERS)


def _is_real_prompt(entry_n: dict, content: object, text: str | None) -> bool:
    """True when a `type:user` entry is a real prompt that opens a turn.

    The authoritative discriminator is `sourceToolAssistantUUID`: Claude
    Code stamps it on every `tool_result` user entry and leaves it absent
    on genuine prompts. We also exclude `isMeta` carriers (workflow-resume
    nudges, queued-command markers), `isSidechain` subagent prompts, and
    first-block tool_results, then fall back to text-level exclusions for
    the synthetic shapes (image carriers, task-notifications, local
    commands) that carry none of those flags."""
    if entry_n.get('source_tool_assistant_uuid'):
        return False
    if entry_n.get('is_meta') or entry_n.get('is_sidechain'):
        return False
    if _first_block_is_tool_result(content):
        return False
    if text and _is_excluded_prompt_text(text):
        return False
    return True


def _extract_tool_result_text(inner: object) -> str | None:
    """Pull human-readable text from an error tool_result's content.

    Used only when `is_error` is True so the synth-deny span can surface
    the user's actual response (e.g. distinguishing "Chat about this"
    from a hard deny on AskUserQuestion).
    """
    if isinstance(inner, str):
        return inner
    if not isinstance(inner, list):
        return None
    parts: list[str] = []
    for item in inner:
        if not isinstance(item, dict):
            continue
        s = item.get('text')
        if isinstance(s, str) and s:
            parts.append(s)
    return '\n'.join(parts) if parts else None


def _extract_advisor_result_text(inner: object) -> str | None:
    """Pull response text from an advisor_tool_result block (dict/str/list)."""
    if isinstance(inner, dict):
        t = inner.get('text')
        return t if isinstance(t, str) and t else None
    if isinstance(inner, str):
        return inner
    if not isinstance(inner, list):
        return None
    parts: list[str] = []
    for item in inner:
        if not isinstance(item, dict):
            continue
        s = item.get('text')
        if isinstance(s, str):
            parts.append(s)
    return '\n'.join(parts) if parts else None


def _advisor_iteration_tokens(usage: object) -> tuple[int, int]:
    """Sum (input, output) across `usage.iterations[*]` advisor_message rows.

    Server-side tools (advisor, web_search, web_fetch) bill via this
    iterations array instead of the main turn's output_tokens. The 1:1
    case (one advisor call per turn) is what the harness emits today;
    we fold every advisor_message iteration into a single bucket so the
    server-side server_tool_use estimate matches API billing.
    """
    if not isinstance(usage, dict):
        return 0, 0
    iters = usage.get('iterations') or []
    if not isinstance(iters, list):
        return 0, 0
    in_total = 0
    out_total = 0
    for it in iters:
        if not isinstance(it, dict) or it.get('type') != 'advisor_message':
            continue
        try:
            in_total += int(it.get('input_tokens') or 0)
            out_total += int(it.get('output_tokens') or 0)
        except (TypeError, ValueError):
            pass
    return in_total, out_total


def _build_tool_call(
    tu_id: str,
    block: dict,
    is_server: bool,
    advisor_model: str | None,
    server_in: int,
    server_out: int,
) -> dict:
    """Materialize the tool_call dict appended to a builder's tool_calls list."""
    tool_name = block.get('name')
    call: dict = {
        'id': tu_id,
        'name': tool_name,
        'is_error': None,
        'output_token_estimate': estimate_tool_use_tokens(
            tool_name, block.get('input')
        ),
        'input_token_estimate': None,
        'image_token_estimate': None,
    }
    # AskUserQuestion's input is small (a list of questions) and
    # irreplaceable when the user denies — no PostToolUse fires so no
    # `tool.AskUserQuestion` span lands. turn_trace uses this to
    # synthesize one from the transcript.
    if tool_name == 'AskUserQuestion':
        inp = block.get('input')
        if isinstance(inp, dict):
            qs = inp.get('questions')
            if isinstance(qs, list) and qs:
                call['input_questions'] = qs
    if is_server:
        # Server-side tool: the harness never dispatches it locally so
        # no PostToolUse hook fires and no user-side `tool_result`
        # entry will arrive. Tokens come from the iterations array on
        # this same entry; the response text arrives later as an
        # `advisor_tool_result` block in the NEXT assistant entry,
        # patched in via tool_use_to_turn.
        call['server_side'] = True
        if advisor_model:
            call['advisor_model'] = advisor_model
        if server_out:
            call['output_token_estimate'] = server_out
        if server_in:
            call['input_token_estimate'] = server_in
    return call


def _should_skip_redistribution(
    builder: _TurnBuilder,
    tool_calls: list[dict],
    text: str | None,
    thinking_text: str | None,
) -> bool:
    """True when redistribution must NOT run for this builder.

    Encrypted-thinking case (thinking_blocks > 0 but no captured
    thinking_text — see `has_encrypted_thinking`): the residual
    `API.out − Σ estimates` is dominated by the turn's reasoning tokens,
    not tool_use framing undershoot. Its rightful home is the
    `assistant.thinking` span, whose output `span_posters` computes as
    `max(0, API.out − text_est − Σ raw non-server tool_use)` — which is
    only correct if the tools below stay raw. So skip regardless of
    whether the turn also emitted user-visible text: a Write that merely
    *followed* the reasoning must not absorb it.

    A turn with no thinking blocks (or with captured plaintext thinking,
    whose tokens the thinking span counts directly) still gets
    redistribution — there the residual really is framing undershoot and
    belongs on the tools.
    """
    if not builder.output_tokens or not tool_calls:
        return True
    return has_encrypted_thinking(builder.thinking_blocks, thinking_text)


def _apply_residual_scale(
    main_tool_calls: list[dict],
    main_tool_total: int,
    residual: int,
) -> None:
    """Scale tool_use estimates by (main_total+residual)/main_total.

    Rounding crumbs go onto the last call so the sum hits the target.
    """
    scale = (main_tool_total + residual) / main_tool_total
    running = 0
    last_tc = None
    for tc in main_tool_calls:
        base = int(tc.get('output_token_estimate') or 0)
        if base <= 0:
            continue
        scaled = int(round(base * scale))
        tc['output_token_estimate'] = scaled
        running += scaled
        last_tc = tc
    target = main_tool_total + residual
    if last_tc is not None and running != target:
        last_tc['output_token_estimate'] = (
            int(last_tc['output_token_estimate'] or 0) + (target - running)
        )


def _redistribute_output_residual(
    builder: _TurnBuilder,
    tool_calls: list[dict],
    text: str | None,
    thinking_text: str | None,
) -> None:
    """Scale tool_use estimates so per-turn block sum equals API.output exactly.

    The API reports one `output_tokens` per turn. We attribute it
    across text / thinking / tool_use blocks via local estimates:

      * text and thinking estimates are tokenized with cl100k_base —
        within a few percent of Claude's actual tokenizer for prose.
      * tool_use estimates are `name + json.dumps(input)`, which
        systematically *undershoots* because it ignores per-block
        framing overhead. Across a session, that undershoot is the
        source of the "untagged_output_tokens" remainder in the rollup.

    When the per-turn residual `API.output − Σ(local estimates)` is
    positive AND the turn has locally-dispatched tool_use blocks, we
    scale each tool_use estimate proportionally so the per-turn block
    sum equals the API-billed output exactly. Server-side tool calls
    (advisor, web_search, web_fetch) are excluded — their estimates
    represent the sub-call's internal token cost, NOT a share of the
    main turn's output_tokens.

    Silent turns (no text, no thinking, just tool_use) DO get
    redistribution because no assistant span is emitted for them —
    the residual would otherwise surface as untagged.
    """
    if _should_skip_redistribution(builder, tool_calls, text, thinking_text):
        return
    main_tool_calls = [tc for tc in tool_calls if not tc.get('server_side')]
    main_tool_total = sum(
        int(tc.get('output_token_estimate') or 0) for tc in main_tool_calls
    )
    if main_tool_total <= 0:
        return
    text_est = estimate_text_tokens(text) if text else 0
    think_est = estimate_text_tokens(thinking_text) if thinking_text else 0
    residual = builder.output_tokens - text_est - think_est - main_tool_total
    if residual <= 0:
        return
    _apply_residual_scale(main_tool_calls, main_tool_total, residual)


def _has_tokens(builder: _TurnBuilder) -> bool:
    return bool(
        builder.input_tokens or builder.output_tokens
        or builder.cache_read_tokens or builder.cache_creation_tokens
    )


def _join_and_truncate(
    parts: list[str],
    max_text_bytes: int | None,
) -> tuple[str | None, bool]:
    """Join non-empty parts with `\\n\\n` and apply the optional byte cap."""
    joined = '\n\n'.join(p for p in parts if p)
    text: str | None = joined or None
    if text is None or max_text_bytes is None or max_text_bytes <= 0:
        return text, False
    return _truncate_utf8(text, max_text_bytes)


def _builder_to_turn_usage(
    builder: _TurnBuilder,
    max_text_bytes: int | None,
) -> TurnUsage:
    """Finalize one builder into the frozen TurnUsage tuple.

    Joins accumulated text/thinking fragments, applies the optional
    per-turn byte cap, computes inference latency from the prior
    entry's timestamp, and runs residual redistribution so each
    tool_call's `output_token_estimate` is the final per-turn share.
    """
    text, truncated = _join_and_truncate(builder.text_parts, max_text_bytes)
    thinking_text, thinking_truncated = _join_and_truncate(
        builder.thinking_parts, max_text_bytes
    )
    inference_ms = _delta_ms(builder.prior_entry_timestamp, builder.timestamp)
    # Redistribution scales `output_token_estimate` in place, so it must run
    # on a copy — `_builder_to_turn_usage` may be called more than once on the
    # same builder (the live resumable rescan finalizes on every poll), and a
    # second pass would scale already-scaled estimates. Shallow-copying each
    # call dict keeps the builder's originals (and their is_error patches) intact.
    tool_calls = [dict(tc) for tc in builder.tool_calls]
    _redistribute_output_residual(builder, tool_calls, text, thinking_text)
    return TurnUsage(
        model=builder.model,
        input_tokens=builder.input_tokens,
        output_tokens=builder.output_tokens,
        cache_read_tokens=builder.cache_read_tokens,
        cache_creation_tokens=builder.cache_creation_tokens,
        cache_creation_1h_tokens=builder.cache_creation_1h_tokens,
        uuid=builder.uuid,
        timestamp=builder.timestamp,
        request_id=builder.request_id,
        message_id=builder.message_id,
        text=text,
        text_truncated=truncated,
        thinking_text=thinking_text,
        thinking_text_truncated=thinking_truncated,
        thinking_blocks=builder.thinking_blocks,
        thinking_signature_bytes=builder.thinking_signature_bytes,
        inference_duration_ms=inference_ms,
        turn_total_duration_ms=builder.turn_total_duration_ms,
        prompt_uuid=builder.prompt_uuid,
        tool_calls=tuple(tool_calls),
        is_api_error=builder.is_api_error,
    )
