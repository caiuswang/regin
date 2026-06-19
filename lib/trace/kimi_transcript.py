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

``read_usage_kimi`` returns the same :class:`TranscriptUsage` /
:class:`TurnUsage` dataclasses Claude's ``read_usage`` produces, so every
downstream span/usage poster works unchanged.
"""

from __future__ import annotations

import json
from datetime import datetime

from lib.trace.transcript_models import TranscriptUsage, TurnUsage
from lib.trace.transcript_parsers import _truncate_utf8
from lib.trace.tool_input_summary import summarize_tool_input
from lib.tokens.token_estimator import estimate_tool_use_tokens

_DEFAULT_TEXT_CAP = 50_000


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

    def __init__(self):
        self.model: str | None = None
        self.prompts: list[tuple[str, str, str | None]] = []  # (uuid, text, ts)
        self.current_prompt: str | None = None
        self.steps: dict[str, _Step] = {}
        self.order: list[str] = []
        self.tool_to_turn: dict[str, str] = {}
        self.calls_by_id: dict[str, dict] = {}
        self.call_args: dict[str, dict] = {}
        self.denials: list[dict] = []

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
        rtype = rec.get('type')
        if rtype == 'turn.prompt':
            self._on_prompt(rec)
        elif rtype == 'usage.record':
            model = rec.get('model')
            if isinstance(model, str) and model:
                self.model = model
        elif rtype == 'context.append_loop_event':
            self._on_loop_event(rec.get('event') or {}, rec.get('time'))
        elif rtype == 'permission.record_approval_result':
            self._on_permission(rec)

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
        parts = [p.get('text', '') for p in (rec.get('input') or [])
                 if isinstance(p, dict) and p.get('type') == 'text']
        text = ''.join(parts).strip()
        uuid = f'kprompt-{len(self.prompts)}'
        self.prompts.append((uuid, text, _iso(rec.get('time'))))
        self.current_prompt = uuid

    def _on_loop_event(self, ev: dict, rec_time: object) -> None:
        kind = ev.get('type')
        if kind == 'step.begin':
            self._step(ev.get('uuid'))
        elif kind == 'content.part':
            self._on_content_part(ev)
        elif kind == 'tool.call':
            self._on_tool_call(ev)
        elif kind == 'tool.result':
            self._on_tool_result(ev)
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

    def _on_tool_call(self, ev: dict) -> None:
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
        }
        step.tool_calls.append(call)
        self.calls_by_id[call_id] = call
        self.tool_to_turn[call_id] = step.uuid
        args = ev.get('args')
        if isinstance(args, dict):
            self.call_args[call_id] = args

    def _on_tool_result(self, ev: dict) -> None:
        call_id = ev.get('toolCallId') or ev.get('parentUuid')
        call = self.calls_by_id.get(call_id) if call_id else None
        if call is None:
            return
        result = ev.get('result')
        is_error = bool(ev.get('isError') or ev.get('is_error'))
        if isinstance(result, dict):
            is_error = is_error or bool(result.get('isError') or result.get('error'))
        call['is_error'] = is_error

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
    scan = _Scan()
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
