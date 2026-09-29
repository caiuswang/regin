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
from dataclasses import dataclass, field

from lib.trace.transcript_models import (
    TranscriptAttachment,
    TranscriptSystemEvent,
    TranscriptUsage,
)
from lib.trace.transcript_parsers import (
    _assistant_message,
    _extract_text_blocks,
    _normalize_dict_keys,
    _scan_thinking_blocks,
    _usage_tokens,
    _walk_to_assistant,
)
from lib.trace.transcript_scan_finalize import _FinalizeScanMixin
from lib.trace.transcript_scan_helpers import (
    _TRACED_ATTACHMENT_KINDS,
    _TRACED_SYSTEM_SUBTYPES,
    _TurnBuilder,
    _advisor_iteration_tokens,
    _build_tool_call,
    _entry_types,
    _extract_advisor_result_text,
    _maybe_str,
    queued_prompt_content,
)
from lib.trace.transcript_scan_user import _UserEntryScanMixin


@dataclass
class _TranscriptScan(_UserEntryScanMixin, _FinalizeScanMixin):
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
    # Message ids (== a builder's dedup key) of assistant turns the user
    # interrupted mid tool-call. Claude Code records that interrupt as a
    # standalone user *text* entry carrying `interruptedMessageId` — NOT a
    # `tool_result` — so the interrupted tool_use blocks never get a paired
    # result (`is_error` stays None) and no PostToolUse fires live. Without
    # this the cancelled call vanishes from the trace; `finalize` reads this
    # to flag those calls so the synth path emits a span. The other variant
    # (interrupt delivered AS a `tool_result` with is_error=True) is left to
    # its live `tool.failure` span — the is_error gate skips it here.
    interrupted_message_ids: set[str] = field(default_factory=set)
    # Uuids of every real-prompt user entry (typed prompts + queued
    # commands; NOT tool_results, task-notifications, local-command
    # echoes, image carriers, meta, or sidechain entries). Each turn's
    # prompt anchor is resolved at finalize by walking parentUuid back
    # into this set (`_resolve_prompt_anchors`) — the parentUuid chain is
    # the transcript's ground truth, so no chronological "last user
    # entry" heuristic is needed.
    real_prompt_uuids: set[str] = field(default_factory=set)
    # Local-command grouping: collect the three related entries (caveat,
    # command-name, stdout) by uuid as we walk past them, then assemble
    # one TranscriptLocalCommand per command-name at end-of-pass.
    lc_commands: list[dict] = field(default_factory=list)
    lc_stdout_by_parent: dict[str, dict] = field(default_factory=dict)
    lc_caveat_uuids: set[str] = field(default_factory=set)
    # uuid → submitted text / ISO timestamp for every non-tool_result user
    # entry. Used at finalize to attach the triggering prompt's text and
    # original time to each turn anchor (the anchor must sit at prompt time
    # on the timeline, not at re-emission time).
    prompt_texts: dict[str, str] = field(default_factory=dict)
    prompt_timestamps: dict[str, str] = field(default_factory=dict)
    # uuid → inline base64 image parts for that prompt entry (durable
    # fallback for prompt_images when the live image cache is gone).
    prompt_image_parts: dict[str, list] = field(default_factory=dict)
    # uuid → Claude Code `promptId` (snake-cased by _normalize_dict_keys) of
    # that prompt entry. This is the *value* the hook envelope mirrors onto
    # every tool span as `source_prompt_id`; stamping it onto the prompt
    # anchor gives serve-time a shared value to join tool→prompt on. NOT the
    # entry's own `uuid` — the anchor id `prompt-<uuid>` is keyed on uuid.
    prompt_ids: dict[str, str] = field(default_factory=dict)
    # Slash-command echoes are excluded from `real_prompt_uuids` (they're
    # `harness.local_command` cards, not typed prompts). But a command that
    # *opens* a turn — `/review`, which expands into instructions an
    # assistant then acts on — must anchor that turn, or its responses walk
    # past it to the PREVIOUS typed prompt (off-by-one). We collect every
    # slash-command echo as a *candidate* anchor; `_resolve_prompt_anchors`
    # walks into this set too, so a command only becomes an anchor when an
    # assistant actually chains up to it before any real prompt (turn-
    # initiating). Meta commands with no in-turn response (`/clear`,
    # `/compact`) are never reached, so they stay local-command-only.
    command_prompt_uuids: set[str] = field(default_factory=set)
    # uuid → display text ("/review") for command candidates, promoted into
    # `prompt_texts` for the subset that actually anchor a turn.
    command_prompt_texts: dict[str, str] = field(default_factory=dict)
    # `<task-notification>` user entries — background-task completions that
    # open a turn but nest under the PRIOR prompt (never their own anchor).
    # They're a turn boundary for command resolution: a command reached only
    # by crossing one (e.g. `/workflows`, whose workflow run chains up via
    # task-notifications) is NOT that turn's opener, so it must not anchor.
    task_notification_uuids: set[str] = field(default_factory=set)
    # parentUuids of isMeta user entries. A command only anchors a turn when
    # it has a direct isMeta child (its skill expansion), so this gates
    # `command_prompt_uuids` down to genuine prompt-expanding commands.
    meta_expansion_parents: set[str] = field(default_factory=set)
    # parent uuid → full expansion text captured from isMeta child entries.
    # Keyed by parent so each slash command's expansion is kept separate.
    meta_expansion_texts: dict[str, str] = field(default_factory=dict)
    # uuid → ISO timestamp for every entry that has both (positions the
    # `rewind` marker and corroborates fork gaps). Distinct from
    # `prev_entry_timestamp`, which tracks only the latency baseline.
    entry_ts: dict[str, str] = field(default_factory=dict)
    # `/rewind` detection inputs, captured during the single scan:
    #   * the latest `last-prompt` row's `leafUuid` (the active branch tip
    #     Claude Code itself records) and the file-tail uuid seed the
    #     live-branch walk;
    #   * raw `file-history-snapshot` rows feed the code-rollback diff.
    last_prompt_leaf: str | None = None
    file_tail_uuid: str | None = None
    snapshot_rows: list[dict] = field(default_factory=list)

    def process_entry(self, entry: object) -> None:
        entry_n = _normalize_dict_keys(entry)
        etype, role = _entry_types(entry_n)
        euuid = _maybe_str(entry_n.get('uuid'))
        eparent = _maybe_str(entry_n.get('parent_uuid'))
        is_user = (etype == 'user' or role == 'user')

        # `/rewind` bookkeeping rows carry no uuid/message of their own and
        # must not flow into the turn/anchor machinery — handle and return.
        if self._capture_rewind_inputs(entry, entry_n, etype, euuid):
            return

        if is_user and euuid:
            self._handle_user_entry(entry_n, eparent)
        if euuid:
            self.entry_parent[euuid] = eparent
        if is_user:
            self._track_prev_timestamp(entry_n)

        self._collect_local_command(entry_n, etype, euuid, eparent)

        if etype == 'system':
            self._handle_system(entry_n, euuid, eparent)
            return
        if etype == 'attachment':
            self._handle_attachment(entry_n, euuid, eparent)
            return
        self._handle_assistant(entry_n, euuid, eparent)

    def _track_prev_timestamp(self, entry_n: dict) -> None:
        ts = entry_n.get('timestamp')
        if isinstance(ts, str) and ts:
            self.prev_entry_timestamp = ts

    def _capture_rewind_inputs(
        self,
        entry: object,
        entry_n: dict,
        etype: str | None,
        euuid: str | None,
    ) -> bool:
        """Capture `/rewind` detection inputs. Returns True when the row is a
        bookkeeping row (`last-prompt` / `file-history-snapshot`) that the
        caller must NOT feed into the turn/anchor machinery."""
        if etype == 'last-prompt':
            leaf = _maybe_str(entry_n.get('leaf_uuid'))
            if leaf:
                self.last_prompt_leaf = leaf
            return True
        if etype == 'file-history-snapshot':
            self.snapshot_rows.append(entry)
            return True
        if euuid:
            self.file_tail_uuid = euuid
            ts = entry_n.get('timestamp')
            if isinstance(ts, str) and ts:
                self.entry_ts[euuid] = ts
        return False

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
        self._maybe_register_queued_prompt(att_n, euuid)

    def _maybe_register_queued_prompt(self, att_n: dict, euuid: str) -> None:
        """A dequeued user prompt (queued while the agent was busy) arrives as a
        `queued_command` attachment, not a real user entry — so by default the
        parentUuid walk passes straight through it to the prompt that was
        running when it was queued, and the responses it triggered anchor there
        instead of under it. Treat a prompt-mode queued command as a real prompt
        boundary so `_resolve_anchor` stops here and its responses nest under it
        (the recovered `prompt-<uuid>` anchor). Only the queued prompt's own
        descendants chain through this entry, so nothing else is affected.
        Text/timestamp come from the attachment itself
        (span_posters._post_queued_command_span), so it stays out of
        `prompt_texts` — no duplicate anchor from _post_prompt_anchor_spans."""
        if att_n.get('type') != 'queued_command':
            return
        text, images = queued_prompt_content(att_n)
        if text or images:
            self.real_prompt_uuids.add(euuid)

    # ---- assistant -----------------------------------------------------------

    @staticmethod
    def _resolve_model(msg: dict, entry_n: dict) -> str | None:
        model = msg.get('model')
        if isinstance(model, str):
            return model
        top_model = entry_n.get('model')
        return top_model if isinstance(top_model, str) else None

    @staticmethod
    def _turn_model(msg: dict, entry_n: dict, is_api_error: bool) -> tuple[bool, str | None]:
        """`(keep, model)` for one assistant entry.

        Claude Code stamps model=<synthetic> on two unrelated shapes. Session
        init banners and /compact markers are not API calls and are dropped.
        A FAILED request is one: dropping it also drops the user prompt, which
        `_resolve_prompt_anchors` reaches only by walking parentUuid back from
        an assistant entry. It is kept with model=None so the failure can never
        become the session's model (derived from assistant_response spans).
        """
        model = _TranscriptScan._resolve_model(msg, entry_n)
        if model != '<synthetic>':
            return True, model
        return is_api_error, None

    @staticmethod
    def _message_id(msg: dict) -> str | None:
        """The API's own `msg_…` id, or None for the shapes that lack one.

        Only the real `message.id` qualifies — never the `request_id` /
        entry-uuid the dedup key falls back to. It is handed across writers as
        an identity, and a fallback value the SDK side cannot possibly produce
        would be a key that never matches at best, and a wrong match at worst.
        """
        mid = msg.get('id')
        return mid if isinstance(mid, str) and mid else None

    def _resolve_dedup_key(
        self, msg: dict, entry_n: dict, euuid: str | None,
        is_api_error: bool = False,
    ) -> str:
        # A failed request is its own event, so it is keyed on its entry uuid
        # rather than a message id it may share with a successful entry:
        # merging the two would flag a billed turn as failed, zero its span
        # output_tokens, and splice the error string into its response text.
        key = None if is_api_error else (msg.get('id') or entry_n.get('request_id'))
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
        is_api_error = bool(entry_n.get('is_api_error_message'))
        keep, model = self._turn_model(msg, entry_n, is_api_error)
        if not keep:
            return
        if euuid:
            self.entry_kind[euuid] = 'assistant'
            self.last_assistant_uuid = euuid

        usage = (_normalize_dict_keys(msg.get('usage'))
                 or _normalize_dict_keys(entry_n.get('usage')))
        dedup_key = self._resolve_dedup_key(msg, entry_n, euuid, is_api_error)
        builder = self.builders.get(dedup_key) or self._new_builder(
            dedup_key, entry_n, euuid, eparent, model,
        )
        builder.message_id = builder.message_id or self._message_id(msg)
        builder.is_api_error = builder.is_api_error or is_api_error
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
            # prompt_uuid is resolved at finalize by walking parentUuid
            # into real_prompt_uuids (`_resolve_prompt_anchors`).
            prompt_uuid=None,
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


@dataclass
class ResumableScanState:
    """Persistent state for an incremental transcript rescan.

    Holds the live `_TranscriptScan` accumulator, the byte offset of the
    last consumed newline (the committed offset), and the file inode.
    `read_usage_resumable` seeks to `offset` and feeds only the appended
    complete lines into `scan`, so a steady session costs O(new bytes) per
    poll instead of re-parsing the whole transcript. Treat as opaque and
    thread the returned instance back into the next call."""

    scan: _TranscriptScan = field(default_factory=_TranscriptScan)
    offset: int = 0
    inode: int | None = None


def _resumable_should_reset(
    state: ResumableScanState | None, st: os.stat_result,
) -> bool:
    """True when the parsed prefix is no longer valid and a fresh
    accumulator is needed: no prior state, the file was replaced (inode
    changed — compaction / `/clear` forward-copy), or it shrank below our
    committed offset (truncation/rewrite). Append-only growth — the normal
    case — returns False, so the common path is one `stat`."""
    if state is None:
        return True
    return state.inode != st.st_ino or st.st_size < state.offset


def _feed_complete_lines(scan: _TranscriptScan, chunk: bytes) -> int:
    """Feed every complete (newline-terminated) JSONL line in `chunk` into
    `scan`; return the number of bytes consumed. A trailing fragment with no
    terminating newline is a partial flush — left unconsumed so the next
    poll reassembles it with the bytes that complete it."""
    nl = chunk.rfind(b'\n')
    if nl == -1:
        return 0
    complete = chunk[:nl + 1]
    for raw in complete.split(b'\n'):
        line = raw.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        scan.process_entry(entry)
    return len(complete)


def read_usage_resumable(
    path: str,
    state: ResumableScanState | None,
    *,
    max_text_bytes: int | None = None,
) -> tuple[TranscriptUsage | None, ResumableScanState]:
    """Incremental `read_usage`: parse only the bytes appended since the
    last call, reusing the persistent accumulator carried in `state`.

    Returns `(usage, state)` — pass `state=None` on the first call, then
    keep threading the returned state back in. Whenever the file was
    replaced or truncated it transparently falls back to a full re-scan, so
    the result is equivalent to a one-shot `read_usage` over the file's
    newline-terminated content. The one intentional divergence: a final
    complete-but-unterminated line (a mid-write flush) is held until its
    newline arrives, whereas `read_usage` would ingest it — both reconverge
    on the next poll once the `\\n` lands. `usage` is None on a
    missing/unreadable file or one with no emit-worthy rows (same as
    `read_usage`).

    `finalize` is called on every invocation and is pure w.r.t. the
    accumulator (see `_TranscriptScan._resolve_prompt_anchors`), so
    re-finalizing a growing scan never corrupts anchors or token shares."""
    if not isinstance(path, str) or not path:
        return None, state or ResumableScanState()
    try:
        st = os.stat(path)
    except OSError:
        return None, state or ResumableScanState()
    if _resumable_should_reset(state, st):
        state = ResumableScanState(inode=st.st_ino)
    if st.st_size > state.offset:
        try:
            with open(path, 'rb') as f:
                f.seek(state.offset)
                chunk = f.read()
        except OSError:
            chunk = b''
        state.offset += _feed_complete_lines(state.scan, chunk)
    return state.scan.finalize(max_text_bytes=max_text_bytes), state
