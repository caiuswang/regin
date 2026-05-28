"""Read token usage + assistant response text from a Claude Code transcript JSONL.

Each assistant line in the transcript has the shape:
    {"type": "assistant", "uuid": "...", "parentUuid": "...",
     "message": {"model": "...",
       "content": [{"type": "text", "text": "..."},
                   {"type": "tool_use", ...}],
       "usage": {"input_tokens": N, "output_tokens": N,
                 "cache_creation_input_tokens": N,
                 "cache_read_input_tokens": N}}}

We walk the file once per hook invocation and report per-turn:
  * token counts (the original purpose), and
  * the response text Claude emitted (text-block content blocks
    concatenated; tool_use and thinking blocks are skipped).

`context_used` is the size of the prompt sent to the model on a given
turn:

    context_used = input_tokens + cache_read + cache_creation

This matches Claude Code's own statusline `context_window.used_tokens`
computation so a session's peak % here will agree with what the user
saw in their terminal on that same turn.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

from lib.tokens.token_estimator import (
    estimate_content_tokens,
    estimate_image_only_tokens,
    estimate_text_tokens,
    estimate_tool_use_tokens,
)
from lib.trace.transcript_models import (
    TranscriptAttachment,
    TranscriptLocalCommand,
    TranscriptSystemEvent,
    TranscriptUsage,
    TurnUsage,
)
from lib.trace.transcript_parsers import (
    _assistant_message,
    _delta_ms,
    _extract_text_blocks,
    _normalize_dict_keys,
    _scan_thinking_blocks,
    _truncate_utf8,
    _usage_tokens,
    _walk_to_assistant,
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
# `span_posters._post_queued_command_span`.
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
_TRACED_SYSTEM_SUBTYPES: frozenset[str] = frozenset({
    'turn_duration',
    'stop_hook_summary',
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
    uuid: str | None = None
    timestamp: str | None = None
    request_id: str | None = None
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
    tool_calls: list[dict] = field(default_factory=list)


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
    text: str | None,
    thinking_text: str | None,
) -> bool:
    """True when redistribution must NOT run for this builder.

    Redacted-thinking-with-tools case (no text, no thinking text, but
    thinking_blocks > 0): turn_trace.py's fallback already computes the
    thinking span's output as `max(0, API.out − Σ tool_use)`, absorbing
    the residual into thinking. Redistributing into tools there would
    zero out the thinking span's output column.
    """
    if not builder.output_tokens or not builder.tool_calls:
        return True
    return not text and not thinking_text and builder.thinking_blocks > 0


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
    if _should_skip_redistribution(builder, text, thinking_text):
        return
    main_tool_calls = [tc for tc in builder.tool_calls if not tc.get('server_side')]
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
    _redistribute_output_residual(builder, text, thinking_text)
    return TurnUsage(
        model=builder.model,
        input_tokens=builder.input_tokens,
        output_tokens=builder.output_tokens,
        cache_read_tokens=builder.cache_read_tokens,
        cache_creation_tokens=builder.cache_creation_tokens,
        uuid=builder.uuid,
        timestamp=builder.timestamp,
        request_id=builder.request_id,
        text=text,
        text_truncated=truncated,
        thinking_text=thinking_text,
        thinking_text_truncated=thinking_truncated,
        thinking_blocks=builder.thinking_blocks,
        thinking_signature_bytes=builder.thinking_signature_bytes,
        inference_duration_ms=inference_ms,
        turn_total_duration_ms=builder.turn_total_duration_ms,
        prompt_uuid=builder.prompt_uuid,
        tool_calls=tuple(builder.tool_calls),
    )


@dataclass
class _TranscriptScan:
    """Mutable scan-wide state for one transcript file pass.

    `read_usage` is a thin driver around this: open the file, dispatch
    each JSONL entry to `process_entry`, then call `finalize`. All the
    cross-entry bookkeeping (parentUuid graph, tool_use → turn mapping,
    in-flight builders, attachment/system/local-command queues) lives
    here so per-entry handlers stay short.
    """

    builders: dict[str, _TurnBuilder] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)
    latest_model: str | None = None
    attachments: list[TranscriptAttachment] = field(default_factory=list)
    system_events: list[TranscriptSystemEvent] = field(default_factory=list)
    # uuid → kind cursor so `system` entries can chain back through
    # `stop_hook_summary` to the assistant turn they belong to.
    entry_kind: dict[str, str] = field(default_factory=dict)
    entry_parent: dict[str, str | None] = field(default_factory=dict)
    last_assistant_uuid: str | None = None
    # Timestamp of the immediately-preceding transcript entry (user
    # prompt or tool_result) — used to derive per-API-call latency for
    # the next builder.
    prev_entry_timestamp: str | None = None
    # Maps tool_use id → the builder of the assistant turn that issued
    # it. Built incrementally so a later `tool_result` user entry can
    # patch is_error onto the originating turn's tool_calls list.
    tool_use_to_turn: dict[str, _TurnBuilder] = field(default_factory=dict)
    # Parallel cache of each tool_use's `input` block, looked up only
    # when the matching tool_result patches is_error=True. Lazy so the
    # 99.x% non-denied case never copies large Bash commands or Edit
    # old/new strings onto the call dict.
    tool_use_inputs: dict[str, object] = field(default_factory=dict)
    # The uuid of the most recent non-tool_result user entry. Captured
    # onto each _TurnBuilder when its first assistant entry is seen.
    # Simpler than parentUuid chain walking because Claude Code injects
    # synthetic user entries (attachment carriers) between the system
    # message and the assistant response.
    current_prompt_uuid: str | None = None
    # Local-command grouping: collect the three related entries (caveat,
    # command-name, stdout) by uuid as we walk past them, then assemble
    # one TranscriptLocalCommand per command-name at end-of-pass.
    lc_commands: list[dict] = field(default_factory=list)
    lc_stdout_by_parent: dict[str, dict] = field(default_factory=dict)
    lc_caveat_uuids: set[str] = field(default_factory=set)

    def process_entry(self, entry: object) -> None:
        entry_n = _normalize_dict_keys(entry)
        etype, role = _entry_types(entry_n)
        euuid = _maybe_str(entry_n.get('uuid'))
        eparent = _maybe_str(entry_n.get('parent_uuid'))
        is_user = (etype == 'user' or role == 'user')

        if is_user and euuid:
            self._handle_user_message(entry_n)
        if euuid:
            self.entry_parent[euuid] = eparent
        if is_user:
            ts = entry_n.get('timestamp')
            if isinstance(ts, str) and ts:
                self.prev_entry_timestamp = ts

        self._collect_local_command(entry_n, etype, euuid, eparent)

        if etype == 'system':
            self._handle_system(entry_n, euuid, eparent)
            return
        if etype == 'attachment':
            self._handle_attachment(entry_n, euuid, eparent)
            return
        self._handle_assistant(entry_n, euuid, eparent)

    # ---- user / tool_result --------------------------------------------------

    def _handle_user_message(self, entry_n: dict) -> None:
        msg = _normalize_dict_keys(entry_n.get('message'))
        content = msg.get('content')
        if not _first_block_is_tool_result(content):
            euuid = _maybe_str(entry_n.get('uuid'))
            if euuid:
                self.current_prompt_uuid = euuid
            return
        if not isinstance(content, list):
            return
        # A single user entry can carry multiple tool_result blocks;
        # patch each onto its originating turn's tool_calls list.
        for block in content:
            if isinstance(block, dict) and block.get('type') == 'tool_result':
                self._patch_tool_result_block(block)

    def _patch_tool_result_block(self, block: dict) -> None:
        tu_id = block.get('tool_use_id')
        if not isinstance(tu_id, str):
            return
        owner = self.tool_use_to_turn.get(tu_id)
        if owner is None:
            return
        is_error = bool(block.get('is_error', False))
        inner = block.get('content')
        input_est = estimate_content_tokens(inner)
        image_est = estimate_image_only_tokens(inner)
        result_text = _extract_tool_result_text(inner) if is_error else None
        for call in owner.tool_calls:
            if call.get('id') != tu_id:
                continue
            call['is_error'] = is_error
            call['input_token_estimate'] = input_est
            call['image_token_estimate'] = image_est
            if result_text is not None:
                call['result_text'] = result_text
            if is_error:
                self._capture_deny_input(call, tu_id)
            return

    def _capture_deny_input(self, call: dict, tu_id: str) -> None:
        raw_input = self.tool_use_inputs.get(tu_id)
        if raw_input is None:
            return
        captured, dropped = _capture_tool_input(raw_input)
        if captured is None:
            return
        call['tool_input'] = captured
        if dropped:
            call['tool_input_truncated_bytes'] = dropped

    # ---- local command -------------------------------------------------------

    def _collect_local_command(
        self,
        entry_n: dict,
        etype: str | None,
        euuid: str | None,
        eparent: str | None,
    ) -> None:
        lc_text = _local_command_text(entry_n, etype)
        if not lc_text or not euuid:
            return
        if '<command-name>' in lc_text:
            self._collect_command_name(entry_n, lc_text, euuid, eparent)
        elif '<bash-input>' in lc_text:
            self._collect_bash_input(entry_n, lc_text, euuid)
        elif '<bash-stdout>' in lc_text and eparent:
            self._collect_bash_stdout(lc_text, euuid, eparent)
        elif '<local-command-stdout>' in lc_text and eparent:
            self._collect_local_stdout(lc_text, euuid, eparent)
        elif _LOCAL_COMMAND_CAVEAT_TAG in lc_text:
            self.lc_caveat_uuids.add(euuid)

    def _collect_bash_stdout(self, lc_text: str, euuid: str, eparent: str) -> None:
        out_m = _BASH_STDOUT_RE.search(lc_text)
        err_m = _BASH_STDERR_RE.search(lc_text)
        err = err_m.group(1) if err_m else None
        self.lc_stdout_by_parent[eparent] = {
            'uuid': euuid,
            'text': out_m.group(1) if out_m else '',
            'stderr': err or None,
        }

    def _collect_local_stdout(self, lc_text: str, euuid: str, eparent: str) -> None:
        m_out = _LOCAL_COMMAND_STDOUT_RE.search(lc_text)
        self.lc_stdout_by_parent[eparent] = {
            'uuid': euuid,
            'text': m_out.group(1) if m_out else '',
        }

    def _collect_command_name(
        self,
        entry_n: dict,
        lc_text: str,
        euuid: str,
        eparent: str | None,
    ) -> None:
        m_name = _COMMAND_NAME_RE.search(lc_text)
        if not m_name:
            return
        m_args = _COMMAND_ARGS_RE.search(lc_text)
        ts = entry_n.get('timestamp')
        self.lc_commands.append({
            'uuid': euuid,
            'name': m_name.group(1).strip(),
            'args': (m_args.group(1).strip() if m_args else None) or None,
            'timestamp': ts if isinstance(ts, str) else None,
            'parent_uuid': eparent,
        })

    def _collect_bash_input(self, entry_n: dict, lc_text: str, euuid: str) -> None:
        # `!ls` → command_name `!ls`. The leading `!` distinguishes a bash
        # command from a slash command in the UI. No caveat in bash mode,
        # so parent_uuid is irrelevant (never matches an lc_caveat_uuid).
        m = _BASH_INPUT_RE.search(lc_text)
        if not m:
            return
        ts = entry_n.get('timestamp')
        self.lc_commands.append({
            'uuid': euuid,
            'name': f'!{m.group(1).strip()}',
            'args': None,
            'timestamp': ts if isinstance(ts, str) else None,
            'parent_uuid': None,
        })

    # ---- system events -------------------------------------------------------

    def _handle_system(
        self,
        entry_n: dict,
        euuid: str | None,
        eparent: str | None,
    ) -> None:
        subtype = entry_n.get('subtype')
        if isinstance(subtype, str) and subtype in _TRACED_SYSTEM_SUBTYPES and euuid:
            self._emit_system_event(entry_n, euuid, eparent, subtype)
        if euuid:
            self.entry_kind[euuid] = (
                f'system:{subtype}' if isinstance(subtype, str) else 'system'
            )

    def _emit_system_event(
        self,
        entry_n: dict,
        euuid: str,
        eparent: str | None,
        subtype: str,
    ) -> None:
        # Resolve the owning assistant turn by walking parentUuid back
        # through any intermediate `system` entries (e.g. turn_duration's
        # parent is the stop_hook_summary that's the parent of the
        # assistant turn).
        resolved = _walk_to_assistant(eparent, self.entry_kind, self.entry_parent)
        if resolved is None and self.last_assistant_uuid:
            resolved = self.last_assistant_uuid
        duration_ms = self._parse_duration_ms(entry_n)
        ts = entry_n.get('timestamp')
        self.system_events.append(TranscriptSystemEvent(
            uuid=euuid,
            parent_uuid=eparent,
            timestamp=ts if isinstance(ts, str) else None,
            subtype=subtype,
            duration_ms=duration_ms,
            turn_uuid=resolved,
            payload=entry_n,
        ))
        if subtype == 'turn_duration' and resolved is not None and duration_ms is not None:
            self._patch_turn_duration(resolved, duration_ms)

    @staticmethod
    def _parse_duration_ms(entry_n: dict) -> int | None:
        raw = entry_n.get('duration_ms') or entry_n.get('durationMs')
        if raw is None:
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    def _patch_turn_duration(self, assistant_uuid: str, duration_ms: int) -> None:
        for b in self.builders.values():
            if b.uuid == assistant_uuid:
                b.turn_total_duration_ms = duration_ms
                return

    # ---- attachments ---------------------------------------------------------

    def _handle_attachment(
        self,
        entry_n: dict,
        euuid: str | None,
        eparent: str | None,
    ) -> None:
        att = entry_n.get('attachment')
        if not isinstance(att, dict) or not euuid:
            return
        att_n = _normalize_dict_keys(att)
        kind = att_n.get('type')
        if not isinstance(kind, str) or kind not in _TRACED_ATTACHMENT_KINDS:
            return
        ts = entry_n.get('timestamp')
        self.attachments.append(TranscriptAttachment(
            uuid=euuid,
            parent_uuid=eparent,
            timestamp=ts if isinstance(ts, str) else None,
            kind=kind,
            payload=att_n,
        ))

    # ---- assistant -----------------------------------------------------------

    @staticmethod
    def _resolve_model(msg: dict, entry_n: dict) -> str | None:
        model = msg.get('model')
        if isinstance(model, str):
            return model
        top_model = entry_n.get('model')
        return top_model if isinstance(top_model, str) else None

    def _resolve_dedup_key(self, msg: dict, entry_n: dict, euuid: str | None) -> str:
        key = msg.get('id') or entry_n.get('request_id')
        if isinstance(key, str) and key:
            return key
        # Without a stable key we'd risk duplicates; treat as its own
        # turn keyed by the entry uuid.
        return euuid or f'_anon_{len(self.order)}'

    def _handle_assistant(
        self,
        entry_n: dict,
        euuid: str | None,
        eparent: str | None,
    ) -> None:
        assistant_kind, msg = _assistant_message(entry_n)
        if assistant_kind != 'assistant':
            return
        model = self._resolve_model(msg, entry_n)
        # Claude Code writes synthetic assistant entries (session init
        # banners, /compact markers) with model=<synthetic> and zero
        # usage. Skip them — they're not real API calls.
        if model == '<synthetic>':
            return
        if euuid:
            self.entry_kind[euuid] = 'assistant'
            self.last_assistant_uuid = euuid

        usage = (_normalize_dict_keys(msg.get('usage'))
                 or _normalize_dict_keys(entry_n.get('usage')))
        dedup_key = self._resolve_dedup_key(msg, entry_n, euuid)
        builder = self.builders.get(dedup_key) or self._new_builder(
            dedup_key, entry_n, euuid, eparent, model,
        )
        self._apply_usage_once(builder, usage)
        content = msg.get('content')
        self._accumulate_text_and_thinking(builder, content)
        if isinstance(content, list):
            self._patch_advisor_tool_results(content)
            self._register_tool_uses(builder, content, usage, entry_n)
        if model:
            self.latest_model = model

    def _new_builder(
        self,
        dedup_key: str,
        entry_n: dict,
        euuid: str | None,
        eparent: str | None,
        model: str | None,
    ) -> _TurnBuilder:
        ts = entry_n.get('timestamp')
        rid = entry_n.get('request_id')
        builder = _TurnBuilder(
            model=model,
            uuid=euuid,
            timestamp=ts if isinstance(ts, str) else None,
            request_id=rid if isinstance(rid, str) else None,
            parent_uuid=eparent,
            prompt_uuid=self.current_prompt_uuid,
            prior_entry_timestamp=self.prev_entry_timestamp,
        )
        self.builders[dedup_key] = builder
        self.order.append(dedup_key)
        return builder

    @staticmethod
    def _apply_usage_once(builder: _TurnBuilder, usage: dict) -> None:
        # First non-empty usage wins: text-block-first entries lack
        # usage; the tool_use entry that follows under the same dedup
        # key carries the actual counters.
        if not usage:
            return
        if (builder.input_tokens or builder.output_tokens
                or builder.cache_read_tokens or builder.cache_creation_tokens):
            return
        (builder.input_tokens, builder.output_tokens,
         builder.cache_read_tokens, builder.cache_creation_tokens) = _usage_tokens(usage)

    @staticmethod
    def _accumulate_text_and_thinking(builder: _TurnBuilder, content: object) -> None:
        # Always append text from this entry's content blocks. Different
        # entries under the same dedup key carry different blocks (text
        # vs tool_use) of the same logical response.
        if isinstance(content, str):
            builder.text_parts.append(content)
            return
        builder.text_parts.extend(_extract_text_blocks(content))
        th_parts, th_count, th_sig = _scan_thinking_blocks(content)
        if th_parts:
            builder.thinking_parts.extend(th_parts)
        builder.thinking_blocks += th_count
        builder.thinking_signature_bytes += th_sig

    def _patch_advisor_tool_results(self, content: list) -> None:
        # Advisor tool results live in a SUBSEQUENT assistant entry
        # (not a user-side `tool_result`), as a block
        # {type:'advisor_tool_result', tool_use_id, content: {text}}.
        # Patch the response text onto the originating server_tool_use
        # call so the synth span can carry it.
        for block in content:
            if not isinstance(block, dict) or block.get('type') != 'advisor_tool_result':
                continue
            tu_id = block.get('tool_use_id')
            if not isinstance(tu_id, str):
                continue
            owner = self.tool_use_to_turn.get(tu_id)
            if owner is None:
                continue
            text = _extract_advisor_result_text(block.get('content'))
            if not text:
                continue
            for call in owner.tool_calls:
                if call.get('id') == tu_id:
                    call['response_text'] = text
                    break

    def _register_tool_uses(
        self,
        builder: _TurnBuilder,
        content: list,
        usage: dict,
        entry_n: dict,
    ) -> None:
        server_in, server_out = _advisor_iteration_tokens(usage)
        raw_advisor = entry_n.get('advisor_model')
        advisor_model = raw_advisor if isinstance(raw_advisor, str) else None
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get('type')
            if btype not in ('tool_use', 'server_tool_use'):
                continue
            tu_id = block.get('id')
            if not isinstance(tu_id, str):
                continue
            call = _build_tool_call(
                tu_id, block,
                is_server=(btype == 'server_tool_use'),
                advisor_model=advisor_model,
                server_in=server_in,
                server_out=server_out,
            )
            # Stash the raw input under the tool_use id so
            # `_capture_tool_input` can resurrect it when the paired
            # tool_result lands as is_error=True. Most calls succeed and
            # never need the input copied out.
            self.tool_use_inputs[tu_id] = block.get('input')
            builder.tool_calls.append(call)
            self.tool_use_to_turn[tu_id] = builder

    # ---- finalize ------------------------------------------------------------

    def _counted_builders(self) -> list[_TurnBuilder]:
        # A turn only counts toward token totals + peak context if it
        # actually carried a `usage` block — entries with content but
        # no usage are subordinate fragments, not standalone turns.
        return [b for b in (self.builders[k] for k in self.order) if _has_tokens(b)]

    def _has_emit_worthy_rows(self, counted: list[_TurnBuilder]) -> bool:
        # We still publish a usage object when only attachments /
        # system events / local-command rows were found so turn_trace
        # can emit the corresponding harness.* spans on a session that
        # hasn't yet had an assistant turn (e.g. /add-dir before the
        # first real prompt).
        return bool(
            counted or self.attachments or self.system_events or self.lc_commands
        )

    def finalize(self, *, max_text_bytes: int | None) -> TranscriptUsage | None:
        counted = self._counted_builders()
        if not self._has_emit_worthy_rows(counted):
            return None
        finalized = [_builder_to_turn_usage(b, max_text_bytes) for b in counted]
        return TranscriptUsage(
            turns=finalized,
            model=self.latest_model,
            input_tokens=sum(t.input_tokens for t in finalized),
            output_tokens=sum(t.output_tokens for t in finalized),
            cache_read_tokens=sum(t.cache_read_tokens for t in finalized),
            cache_creation_tokens=sum(t.cache_creation_tokens for t in finalized),
            peak_context_tokens=max((t.context_used for t in finalized), default=0),
            attachments=tuple(self.attachments),
            system_events=tuple(self.system_events),
            local_commands=tuple(self._finalize_local_commands()),
        )

    def _finalize_local_commands(self) -> list[TranscriptLocalCommand]:
        out: list[TranscriptLocalCommand] = []
        for ce in self.lc_commands:
            stdout = self.lc_stdout_by_parent.get(ce['uuid'])
            caveat_uuid = (
                ce['parent_uuid']
                if ce['parent_uuid'] in self.lc_caveat_uuids
                else None
            )
            out.append(TranscriptLocalCommand(
                command_uuid=ce['uuid'],
                command_name=ce['name'],
                args=ce['args'],
                timestamp=ce['timestamp'],
                stdout_uuid=stdout['uuid'] if stdout else None,
                stdout_text=stdout['text'] if stdout else None,
                caveat_uuid=caveat_uuid,
                stderr_text=stdout.get('stderr') if stdout else None,
            ))
        return out


def read_usage(
    path: str,
    *,
    max_text_bytes: int | None = None,
) -> TranscriptUsage | None:
    """Stream-parse a transcript and summarise token usage + response text.

    Returns None if the file is missing/unreadable or carries no
    assistant turns with usage data.

    A single Anthropic API response is split into multiple content
    blocks in the transcript (text + tool_use → two adjacent
    `assistant` entries sharing the same `message.id`). Each entry
    repeats the exact same `usage` block, so we dedup by `message.id`
    (falling back to `requestId`) for usage counters. **Text blocks
    are accumulated across all entries** that share a dedup key, since
    a logical turn's text may live in any of them.

    `max_text_bytes` (per turn) caps each response text at that UTF-8
    byte size; longer text is truncated with a `…[truncated]` marker.
    Pass None for no cap.
    """
    if not isinstance(path, str) or not path:
        return None
    if not os.path.isfile(path):
        return None

    scan = _TranscriptScan()
    try:
        with open(path, 'rb') as f:
            for raw in f:
                line = raw.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                scan.process_entry(entry)
    except OSError:
        return None
    return scan.finalize(max_text_bytes=max_text_bytes)
