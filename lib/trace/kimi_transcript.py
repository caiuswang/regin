"""Parse Kimi Code CLI session files (`wire.jsonl`) into regin's transcript model.

Kimi stores each session under
``~/.kimi-code/sessions/wd_<proj>_<hash>/<session_id>/agents/main/wire.jsonl``
as a JSONL event stream (protocol_version 1.4). Unlike Claude's transcript,
it is event-sourced rather than message-per-line; the load-bearing records are:

* ``turn.prompt``      — a user prompt (``input: [{type:text,text}]``, ``time``).
* ``context.append_loop_event`` — the assistant work stream, keyed by event
  ``type``: ``step.begin`` / ``step.end`` bracket one model inference (a *step*,
  which maps to a regin *turn*), ``content.part`` carries ``think`` / ``text``
  parts, ``tool.call`` / ``tool.result`` carry tool activity. ``step.end`` holds
  the per-step token ``usage`` (``inputOther`` / ``output`` / ``inputCacheRead``
  / ``inputCacheCreation``).
* ``usage.record``     — turn-scoped token totals; we read ``model`` from here.
* ``context.append_message`` — anything injected into the context that is not
  a typed prompt; ``origin.kind=injection`` bodies wrapped in
  ``<system-reminder>`` are the harness nudges Claude surfaces as
  ``task_reminder`` attachments.
* ``tools.set_active_tools`` / ``llm.tools_snapshot`` — the active tool
  surface, diffed into ``deferred_tools_delta`` attachments.
* ``turn.steer``      — input submitted *while a turn is running*. Only
  ``origin.kind=user`` steers are the user's own words (the rest are
  background-task notifications the CLI feeds back in).
* ``turn.cancel``     — the user interrupted the turn; tool calls still
  awaiting a ``tool.result`` at that point never ran to completion.

A pasted image is not inlined: the prompt keeps an ``image_url`` part whose
url is ``blobref:<media_type>;<sha256>``, resolving against the sibling
``blobs/<sha256>`` file next to ``wire.jsonl``.

``read_usage_kimi`` returns the same :class:`TranscriptUsage` /
:class:`TurnUsage` dataclasses Claude's ``read_usage`` produces, so every
downstream span/usage poster works unchanged.
"""

from __future__ import annotations

import base64
import json
import os
from datetime import datetime

from lib.trace.transcript_models import (
    TranscriptAttachment, TranscriptUsage, TurnUsage,
)
from lib.trace.transcript_parsers import _truncate_utf8
from lib.trace.tool_input_summary import summarize_tool_input
from lib.tokens.token_estimator import estimate_tool_use_tokens

_DEFAULT_TEXT_CAP = 50_000

# Kimi swaps a pasted image out of the prompt for a `<system>…</system>`
# note describing the compression it applied. It is CLI bookkeeping, not
# something the user typed, so it is dropped from the prompt text (the
# image itself is recovered from the sibling `image_url` blobref part).
_IMAGE_BLURB_PREFIX = '<system>Image compressed'

_SYSTEM_REMINDER_TAG = '<system-reminder>'

_BLOBREF_SCHEME = 'blobref:'


# Kimi `permission.record_approval_result` decision values that mean the user
# rejected the call. Anything else (e.g. "approved") needs no span.
_DENIED_DECISIONS = frozenset({'denied', 'rejected', 'deny', 'reject'})


def _summarize_args(args: object) -> dict | None:
    """Compact tool-call args for a deny span — the small, display-worthy keys
    only, capped, so a denied call still shows its command/target in the trace
    without storing whole file bodies. Shares the canonical projection with the
    provider adapters; keeps the larger command cap a denied shell call wants."""
    return summarize_tool_input(args, command_cap=2000) or None


def _iso(ms: object) -> str | None:
    """Convert a Kimi epoch-millis timestamp to a local ISO string."""
    if not isinstance(ms, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(ms / 1000).isoformat()
    except (ValueError, OSError, OverflowError):
        return None


def _cap(text: str, max_bytes: int | None) -> tuple[str | None, bool]:
    """Byte-cap assistant/thinking text, returning (text_or_None, truncated).

    Delegates the UTF-8-boundary cut to the shared `_truncate_utf8` (which also
    appends the `…[truncated]` marker the trace UI shows), keeping a None for
    empty text and treating a 0/None cap as 'no limit'."""
    if not text:
        return None, False
    if not max_bytes or max_bytes <= 0:
        return text, False
    return _truncate_utf8(text, max_bytes)


def _message_text(content: object) -> str:
    """Concatenate the text blocks of a Kimi ``message.content`` list."""
    if not isinstance(content, list):
        return ''
    return ''.join(
        str(c.get('text', '')) for c in content
        if isinstance(c, dict) and c.get('type') == 'text'
    )


def _tool_names(names: list) -> list[str]:
    """Concrete tool names from an active-tools list.

    `tools.set_active_tools` may carry a selector glob (`mcp__*`) where
    `llm.tools_snapshot` carries the tools it resolved to; diffing a selector
    against concrete names invents a removal, so globs are dropped."""
    return [n for n in names if isinstance(n, str) and n and '*' not in n]


def _is_image_blurb(text: str) -> bool:
    return text.startswith(_IMAGE_BLURB_PREFIX) and text.rstrip().endswith('</system>')


def _parse_blobref(url: object) -> tuple[str, str] | None:
    """Split ``blobref:<media_type>;<sha256>`` into its two halves."""
    if not isinstance(url, str) or not url.startswith(_BLOBREF_SCHEME):
        return None
    media, sep, digest = url[len(_BLOBREF_SCHEME):].partition(';')
    if not sep or not media or not digest:
        return None
    return media, digest


def _load_blob(blobs_dir: str, digest: str) -> str | None:
    """Base64 the content-addressed blob, or None when it is gone.

    No size cap here: the persistence caps (`prompt_image_max_bytes`,
    `prompt_images_max_count`) are applied once, downstream, where dropping
    an image still leaves it counted in the anchor's `image_indices`."""
    try:
        with open(os.path.join(blobs_dir, digest), 'rb') as fh:
            return base64.b64encode(fh.read()).decode('ascii')
    except OSError:
        return None


def _image_part(part: dict, blobs_dir: str, idx: int) -> dict | None:
    """Resolve one ``image_url`` part into the inline `{idx, media_type,
    data_b64}` shape `resolve_prompt_images` consumes."""
    url = part.get('imageUrl')
    ref = _parse_blobref(url.get('url')) if isinstance(url, dict) else None
    if ref is None:
        return None
    media_type, digest = ref
    data_b64 = _load_blob(blobs_dir, digest)
    if data_b64 is None:
        return None
    return {'idx': idx, 'media_type': media_type, 'data_b64': data_b64}


def _typed_text(parts: object) -> str:
    """The text the user actually typed in a Kimi ``input`` array."""
    texts: list[str] = []
    for part in parts if isinstance(parts, list) else []:
        if not isinstance(part, dict) or part.get('type') != 'text':
            continue
        text = str(part.get('text', ''))
        if not _is_image_blurb(text):
            texts.append(text)
    return ''.join(texts).strip()


def _prompt_images(parts: object, blobs_dir: str) -> list[dict]:
    """Inline image parts of a Kimi ``input`` array.

    `idx` counts every image part, resolved or not, so a missing blob does
    not renumber the images that follow it."""
    images: list[dict] = []
    idx = 0
    for part in parts if isinstance(parts, list) else []:
        if not isinstance(part, dict) or part.get('type') != 'image_url':
            continue
        idx += 1
        image = _image_part(part, blobs_dir, idx)
        if image is not None:
            images.append(image)
    return images


class _Step:
    """Mutable accumulator for one Kimi step (= one regin turn)."""

    __slots__ = ('uuid', 'prompt_uuid', 'text', 'think', 'tool_calls',
                 'usage', 'time', 'duration_ms')

    def __init__(self, uuid: str, prompt_uuid: str | None):
        self.uuid = uuid
        self.prompt_uuid = prompt_uuid
        self.text: list[str] = []
        self.think: list[str] = []
        self.tool_calls: list[dict] = []
        self.usage: dict = {}
        self.time: object = None
        self.duration_ms: int | None = None


class _Scan:
    """Single-pass accumulator over a Kimi wire.jsonl event stream."""

    def __init__(self, blobs_dir: str):
        self.blobs_dir = blobs_dir
        self.model: str | None = None
        self.prompts: list[tuple[str, str, str | None]] = []  # (uuid, text, ts)
        self.current_prompt: str | None = None
        self.steps: dict[str, _Step] = {}
        self.order: list[str] = []
        self.tool_to_turn: dict[str, str] = {}
        self.calls_by_id: dict[str, dict] = {}
        self.call_args: dict[str, dict] = {}
        self.call_times: dict[str, float] = {}
        self.denials: list[dict] = []
        self.attachments: list[TranscriptAttachment] = []
        self.prompt_images: dict[str, list] = {}
        self.active_tools: set[str] = set()

    def _step(self, uuid: str | None) -> _Step | None:
        if not isinstance(uuid, str) or not uuid:
            return None
        step = self.steps.get(uuid)
        if step is None:
            step = _Step(uuid, self.current_prompt)
            self.steps[uuid] = step
            self.order.append(uuid)
        return step

    def feed(self, rec: dict) -> None:
        handler = _RECORD_HANDLERS.get(rec.get('type'))
        if handler is not None:
            handler(self, rec)

    def _on_usage_record(self, rec: dict) -> None:
        model = rec.get('model')
        if isinstance(model, str) and model:
            self.model = model

    def _on_loop_record(self, rec: dict) -> None:
        self._on_loop_event(rec.get('event') or {}, rec.get('time'))

    def _add_attachment(self, kind: str, payload: dict, ms: object) -> None:
        """Append a synthetic attachment. Kimi records carry no uuid, so the
        id is minted from the append order — it keys the span_id and the
        seen-cache, both of which need it stable across rescans of a
        (append-only) wire file."""
        self.attachments.append(TranscriptAttachment(
            uuid=f'katt-{len(self.attachments)}',
            parent_uuid=self.current_prompt,
            timestamp=_iso(ms),
            kind=kind,
            payload=payload,
        ))

    def _on_append_message(self, rec: dict) -> None:
        message = rec.get('message')
        text = _message_text(message.get('content')) if isinstance(message, dict) else ''
        if _SYSTEM_REMINDER_TAG not in text:
            return
        self._add_attachment('task_reminder', {'content': text}, rec.get('time'))

    def _on_active_tools(self, rec: dict) -> None:
        self._tools_delta(rec.get('names'), rec.get('time'))

    def _on_tools_snapshot(self, rec: dict) -> None:
        tools = rec.get('tools')
        if not isinstance(tools, list):
            return
        self._tools_delta(
            [t.get('name') for t in tools if isinstance(t, dict)], rec.get('time'),
        )

    def _tools_delta(self, names: object, ms: object) -> None:
        if not isinstance(names, list):
            return
        current = _tool_names(names)
        added = [n for n in current if n not in self.active_tools]
        removed = sorted(self.active_tools.difference(current))
        self.active_tools = set(current)
        if not added and not removed:
            return
        self._add_attachment(
            'deferred_tools_delta',
            {'added_names': added, 'removed_names': removed}, ms,
        )

    def _on_steer(self, rec: dict) -> None:
        """A mid-turn submission. Only the user's own words become a prompt;
        `background_task` steers are CLI notifications fed back into the
        context, and already show up as the task's own spans."""
        origin = rec.get('origin')
        if not isinstance(origin, dict) or origin.get('kind') != 'user':
            return
        text = _typed_text(rec.get('input'))
        if not text:
            return
        self._add_attachment(
            'queued_command',
            {'command_mode': 'prompt', 'prompt': text}, rec.get('time'),
        )

    def _on_cancel(self, rec: dict) -> None:
        """Flag every tool call still awaiting a result — the interrupt
        killed them mid-flight, so they never emit a tool.result."""
        for call in self.calls_by_id.values():
            if call.get('is_error') is None:
                call['interrupted'] = True

    def _on_permission(self, rec: dict) -> None:
        """Record a *denied* tool call. Kimi resolves permission prompts in its
        own TUI and only logs the outcome here; an approval needs no span (the
        tool runs and reports normally), but a denial fires no PostToolUse, so
        we surface it as a deny span downstream."""
        result = rec.get('result')
        decision = result.get('decision') if isinstance(result, dict) else None
        if not isinstance(decision, str) or decision.strip().lower() not in _DENIED_DECISIONS:
            return
        tu_id = rec.get('toolCallId')
        if not isinstance(tu_id, str) or not tu_id:
            return
        tool_name = rec.get('toolName')
        action = rec.get('action')
        # tool_input is filled in _build_usage, not here: Kimi emits the
        # approval record *before* the tool.call carrying the args, so
        # call_args isn't populated yet at this point.
        self.denials.append({
            'tool_use_id': tu_id,
            'tool_name': tool_name if isinstance(tool_name, str) and tool_name else 'unknown',
            'denial_reason': action if isinstance(action, str) and action else None,
            'timestamp': _iso(rec.get('time')),
        })

    def _on_prompt(self, rec: dict) -> None:
        parts = rec.get('input')
        uuid = f'kprompt-{len(self.prompts)}'
        self.prompts.append((uuid, _typed_text(parts), _iso(rec.get('time'))))
        self.current_prompt = uuid
        images = _prompt_images(parts, self.blobs_dir)
        if images:
            self.prompt_images[uuid] = images

    def _on_loop_event(self, ev: dict, rec_time: object) -> None:
        kind = ev.get('type')
        if kind == 'step.begin':
            self._step(ev.get('uuid'))
        elif kind == 'content.part':
            self._on_content_part(ev)
        elif kind == 'tool.call':
            self._on_tool_call(ev, rec_time)
        elif kind == 'tool.result':
            self._on_tool_result(ev, rec_time)
        elif kind == 'step.end':
            self._on_step_end(ev, rec_time)

    def _on_content_part(self, ev: dict) -> None:
        step = self._step(ev.get('stepUuid'))
        part = ev.get('part') or {}
        if step is None or not isinstance(part, dict):
            return
        if part.get('type') == 'think':
            step.think.append(str(part.get('think', '')))
        elif part.get('type') == 'text':
            step.text.append(str(part.get('text', '')))

    def _on_tool_call(self, ev: dict, rec_time: object) -> None:
        step = self._step(ev.get('stepUuid'))
        call_id = ev.get('toolCallId') or ev.get('uuid')
        if step is None or not call_id:
            return
        # Mirror the Claude tool_call dict shape the span posters expect
        # ({id, name, is_error, *_token_estimate}); is_error stays None until
        # the matching tool.result patches it.
        name = ev.get('name')
        call = {
            'id': call_id,
            'name': name,
            'is_error': None,
            'output_token_estimate': estimate_tool_use_tokens(name, ev.get('args')),
            'input_token_estimate': None,
            'image_token_estimate': None,
            'source_prompt_id': self.current_prompt,
        }
        step.tool_calls.append(call)
        self.calls_by_id[call_id] = call
        self.tool_to_turn[call_id] = step.uuid
        if isinstance(rec_time, (int, float)):
            self.call_times[call_id] = rec_time
        args = ev.get('args')
        if isinstance(args, dict):
            self.call_args[call_id] = args

    def _on_tool_result(self, ev: dict, rec_time: object) -> None:
        call_id = ev.get('toolCallId') or ev.get('parentUuid')
        call = self.calls_by_id.get(call_id) if call_id else None
        if call is None:
            return
        result = ev.get('result')
        is_error = bool(ev.get('isError') or ev.get('is_error'))
        if isinstance(result, dict):
            is_error = is_error or bool(result.get('isError') or result.get('error'))
        call['is_error'] = is_error
        # A result after a turn.cancel means the tool did finish — the
        # in-flight guess made at cancel time was wrong.
        call.pop('interrupted', None)
        duration = self._call_duration(call_id, rec_time)
        if duration is not None:
            call['duration_ms'] = duration

    def _call_duration(self, call_id: str, rec_time: object) -> int | None:
        """Wall-clock of one tool call, from its own record timestamps. Kimi
        never reports a tool duration itself; the `tool.call` → `tool.result`
        record pair brackets the run exactly."""
        started = self.call_times.get(call_id)
        if started is None or not isinstance(rec_time, (int, float)):
            return None
        return max(0, int(rec_time - started))

    def _on_step_end(self, ev: dict, rec_time: object) -> None:
        step = self._step(ev.get('uuid'))
        if step is None:
            return
        usage = ev.get('usage')
        if isinstance(usage, dict):
            step.usage = usage
        step.time = ev.get('time', rec_time)
        dur = ev.get('llmStreamDurationMs')
        step.duration_ms = int(dur) if isinstance(dur, (int, float)) else None


_RECORD_HANDLERS = {
    'turn.prompt': _Scan._on_prompt,
    'usage.record': _Scan._on_usage_record,
    'context.append_loop_event': _Scan._on_loop_record,
    'permission.record_approval_result': _Scan._on_permission,
    'context.append_message': _Scan._on_append_message,
    'tools.set_active_tools': _Scan._on_active_tools,
    'llm.tools_snapshot': _Scan._on_tools_snapshot,
    'turn.steer': _Scan._on_steer,
    'turn.cancel': _Scan._on_cancel,
}


def _turn_from_step(step: _Step, model: str | None, max_text_bytes: int | None) -> TurnUsage:
    u = step.usage or {}
    text, text_trunc = _cap(''.join(step.text), max_text_bytes)
    think, think_trunc = _cap(''.join(step.think), max_text_bytes)
    return TurnUsage(
        model=model,
        input_tokens=int(u.get('inputOther', 0) or 0),
        output_tokens=int(u.get('output', 0) or 0),
        cache_read_tokens=int(u.get('inputCacheRead', 0) or 0),
        cache_creation_tokens=int(u.get('inputCacheCreation', 0) or 0),
        uuid=step.uuid,
        timestamp=_iso(step.time),
        request_id=None,
        text=text,
        text_truncated=text_trunc,
        thinking_text=think,
        thinking_text_truncated=think_trunc,
        thinking_blocks=len(step.think),
        inference_duration_ms=step.duration_ms,
        prompt_uuid=step.prompt_uuid,
        tool_calls=tuple(step.tool_calls),
    )


def _iter_records(path: str):
    with open(path, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue


def _token_totals(turns: list[TurnUsage]) -> tuple[int, int, int, int, int]:
    """Single-pass (input, output, cache_read, cache_creation, peak_context)."""
    inp = out = cread = ccreate = peak = 0
    for t in turns:
        inp += t.input_tokens
        out += t.output_tokens
        cread += t.cache_read_tokens
        ccreate += t.cache_creation_tokens
        peak = max(peak, t.context_used)
    return inp, out, cread, ccreate, peak


def _prompt_maps(prompts) -> tuple[dict, dict]:
    """Split (uuid, text, ts) prompt tuples into text and timestamp maps."""
    texts: dict[str, str] = {}
    stamps: dict[str, str] = {}
    for uuid, text, ts in prompts:
        if text:
            texts[uuid] = text
        if ts:
            stamps[uuid] = ts
    return texts, stamps


def _build_usage(scan: _Scan, turns: list[TurnUsage]) -> TranscriptUsage:
    inp, out, cread, ccreate, peak = _token_totals(turns)
    texts, stamps = _prompt_maps(scan.prompts)
    # Resolve each denied call's args now that the whole stream is scanned —
    # Kimi logs the rejection before the tool.call that carries the args.
    for d in scan.denials:
        d['tool_input'] = _summarize_args(scan.call_args.get(d['tool_use_id']))
    return TranscriptUsage(
        turns=turns,
        model=(turns[-1].model if turns else scan.model),
        input_tokens=inp,
        output_tokens=out,
        cache_read_tokens=cread,
        cache_creation_tokens=ccreate,
        peak_context_tokens=peak,
        prompt_texts=texts,
        prompt_timestamps=stamps,
        # Kimi has no separate prompt id: the minted anchor uuid IS the
        # value both sides of the ladder join on (anchor attr `prompt_id`
        # ↔ tool span `source_prompt_id`).
        prompt_ids={uuid: uuid for uuid in texts},
        prompt_image_parts={
            uuid: parts for uuid, parts in scan.prompt_images.items()
            if uuid in texts
        },
        attachments=tuple(scan.attachments),
        tool_use_to_turn_uuid=scan.tool_to_turn,
        permission_denials=tuple(scan.denials),
    )


def read_usage_kimi(path: str, *, max_text_bytes: int | None = None) -> TranscriptUsage | None:
    """Parse a Kimi ``wire.jsonl`` into a :class:`TranscriptUsage`.

    Mirrors ``lib.trace.transcript_usage.read_usage``: returns None on I/O
    error or an empty stream, otherwise the same dataclasses the span posters
    consume. ``max_text_bytes`` caps captured assistant/thinking text.
    """
    cap = _DEFAULT_TEXT_CAP if max_text_bytes is None else max_text_bytes
    scan = _Scan(blobs_dir=os.path.join(os.path.dirname(path), 'blobs'))
    try:
        for rec in _iter_records(path):
            if isinstance(rec, dict):
                scan.feed(rec)
    except OSError:
        return None

    turns = [_turn_from_step(scan.steps[u], scan.model, cap) for u in scan.order]
    if not turns and not scan.prompts:
        return None
    return _build_usage(scan, turns)
