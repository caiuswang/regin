"""Parse CodeBuddy-family session transcripts into regin's transcript model.

CodeBuddy Code and the WorkBuddy desktop app (which embeds the same engine)
write one JSONL file per session at ``<root>/projects/<slug>/<sessionId>.jsonl``.
Each line is a typed record:

* ``message``        — ``role`` user/assistant, ``content: [{input_text|output_text}]``.
  A user message opens a new turn unless ``providerData.startsNewUserRequest``
  is explicitly false (older engines never write the flag).
* ``reasoning``      — model thinking, text in ``rawContent`` (or ``content``).
* ``function_call``  — ``callId``, ``name``, ``arguments`` (JSON string).
* ``function_call_result`` — ``callId``, ``output``, ``status``.
* ``ai-title``       — the session's generated title.
* ``session-meta`` / ``file-history-snapshot`` / anything else — ignored.

One model inference is one ``providerData.messageId``: its reasoning, text and
parallel function_calls all share it, and at most one of those records carries
``providerData.usage``. That inference becomes one :class:`TurnUsage`.

Every span id derives from ``(sessionId, record id)`` through `entry_key`.
The ids are UUIDv7, whose leading 13 characters are a millisecond timestamp,
so the span posters' ``[:13]`` truncation of a raw id would collide for
records written in the same millisecond — hence the hash. Records without an
``id`` produce no span.

The ``<slug>/<sessionId>/tool-results/`` overflow files are never read: the
result text kept inline in the transcript is what the span carries.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

from lib.trace.tool_input_summary import summarize_tool_input
from lib.trace.transcript_models import TranscriptUsage, TurnUsage
from lib.trace.transcript_parsers import _truncate_utf8
from lib.tokens.token_estimator import estimate_tool_use_tokens

_DEFAULT_TEXT_CAP = 50_000
_TOOL_OUTPUT_CAP = 8_000

TOOL_SPAN_PREFIX = 'trtool-'
TOOL_PENDING_PREFIX = 'pending-'

_USER_QUERY_OPEN = '<user_query>'
_USER_QUERY_CLOSE = '</user_query>'


def entry_key(session_id: str, entry_id: str) -> str:
    digest = hashlib.sha1(f'{session_id}\x00{entry_id}'.encode('utf-8'))
    return digest.hexdigest()[:13]


def _iso(ms: object) -> str | None:
    if not isinstance(ms, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(ms / 1000).isoformat()
    except (ValueError, OSError, OverflowError):
        return None


def _cap(text: str, max_bytes: int | None) -> tuple[str | None, bool]:
    if not text:
        return None, False
    if not max_bytes or max_bytes <= 0:
        return text, False
    return _truncate_utf8(text, max_bytes)


def _str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _block_text(blocks: object, kinds: tuple[str, ...]) -> str:
    if isinstance(blocks, str):
        return blocks
    if not isinstance(blocks, list):
        return ''
    return ''.join(
        str(b.get('text', '')) for b in blocks
        if isinstance(b, dict) and b.get('type') in kinds
    )


def prompt_text(content: object) -> str:
    """What the user typed. The desktop app wraps it in a large
    ``<system-reminder>`` context block; the typed words are its
    ``<user_query>``. Headless sessions send plain text.

    The last opening tag wins: an automation run's context block mentions a
    bare ``<user_query>`` in its instructions, ahead of the real one."""
    raw = _block_text(content, ('input_text', 'text'))
    start = raw.rfind(_USER_QUERY_OPEN)
    if start < 0:
        return raw.strip()
    body = raw[start + len(_USER_QUERY_OPEN):]
    end = body.find(_USER_QUERY_CLOSE)
    return (body if end < 0 else body[:end]).strip()


def _detail_sum(details: object, key: str) -> int:
    if not isinstance(details, list):
        return 0
    return sum(int(d.get(key) or 0) for d in details if isinstance(d, dict))


def _output_text(output: object) -> str:
    if isinstance(output, str):
        return output
    if isinstance(output, dict):
        text = output.get('text')
        if isinstance(text, str):
            return text
        return json.dumps(output, ensure_ascii=False)
    if isinstance(output, list):
        return _block_text(output, ('text', 'output_text', 'input_text'))
    return ''


def _parse_args(arguments: object) -> dict | None:
    if isinstance(arguments, dict):
        return arguments
    if not isinstance(arguments, str) or not arguments:
        return None
    try:
        parsed = json.loads(arguments)
    except (json.JSONDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


class _Step:
    """One model inference (one ``providerData.messageId``)."""

    __slots__ = ('uuid', 'message_id', 'prompt_uuid', 'model', 'text', 'think',
                 'tool_calls', 'usage', 'ms')

    def __init__(self, uuid: str, message_id: str | None, prompt_uuid: str | None):
        self.uuid = uuid
        self.message_id = message_id
        self.prompt_uuid = prompt_uuid
        self.model: str | None = None
        self.text: list[str] = []
        self.think: list[str] = []
        self.tool_calls: list[dict] = []
        self.usage: dict | None = None
        self.ms: object = None


class _Call:
    __slots__ = ('key', 'call', 'args', 'start_ms', 'end_ms', 'output', 'status',
                 'turn_uuid')

    def __init__(self, key: str, call: dict, args: dict | None, start_ms: object,
                 turn_uuid: str):
        self.key = key
        self.call = call
        self.args = args
        self.start_ms = start_ms
        self.end_ms: object = None
        self.output: str | None = None
        self.status: str | None = None
        self.turn_uuid = turn_uuid


class _Scan:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.prompts: list[tuple[str, str, str | None, str]] = []
        self.current_prompt: str | None = None
        self.current_turn_key: str | None = None
        self.steps: dict[str, _Step] = {}
        self.order: list[str] = []
        self.calls: dict[str, _Call] = {}
        self.call_order: list[str] = []
        self.title: str | None = None

    def feed(self, rec: dict) -> None:
        handler = _HANDLERS.get(rec.get('type'))
        if handler is not None:
            handler(self, rec)

    def _key(self, rec: dict) -> str | None:
        rid = _str(rec.get('id'))
        return entry_key(self.session_id, rid) if rid else None

    def _on_message(self, rec: dict) -> None:
        if rec.get('role') == 'user':
            self._on_user(rec)
        elif rec.get('role') == 'assistant':
            step = self._step(rec)
            if step is not None:
                step.text.append(_block_text(rec.get('content'), ('output_text', 'text')))

    def _on_user(self, rec: dict) -> None:
        pd = rec.get('providerData') if isinstance(rec.get('providerData'), dict) else {}
        if pd.get('startsNewUserRequest') is False:
            return
        rid = _str(rec.get('id'))
        self.current_turn_key = _str(pd.get('conversationRequestId')) or rid
        key = self._key(rec)
        self.current_prompt = key
        if key is None:
            return
        self.prompts.append((key, prompt_text(rec.get('content')),
                             _iso(rec.get('timestamp')), self.current_turn_key or key))

    def _step(self, rec: dict) -> _Step | None:
        key = self._key(rec)
        if key is None:
            return None
        pd = rec.get('providerData') if isinstance(rec.get('providerData'), dict) else {}
        message_id = _str(pd.get('messageId'))
        group = message_id or key
        step = self.steps.get(group)
        if step is None:
            step = _Step(key, message_id, self.current_prompt)
            self.steps[group] = step
            self.order.append(group)
        step.model = _str(pd.get('model')) or step.model
        usage = pd.get('usage')
        if step.usage is None and isinstance(usage, dict):
            step.usage = usage
        if isinstance(rec.get('timestamp'), (int, float)):
            step.ms = rec['timestamp']
        return step

    def _on_reasoning(self, rec: dict) -> None:
        step = self._step(rec)
        if step is None:
            return
        text = (_block_text(rec.get('rawContent'), ('reasoning_text', 'text'))
                or _block_text(rec.get('content'), ('reasoning_text', 'text')))
        if text:
            step.think.append(text)

    def _on_function_call(self, rec: dict) -> None:
        step = self._step(rec)
        call_id = _str(rec.get('callId'))
        name = _str(rec.get('name'))
        if step is None or call_id is None or name is None:
            return
        pd = rec.get('providerData') if isinstance(rec.get('providerData'), dict) else {}
        args = _parse_args(rec.get('arguments'))
        call = {
            'id': call_id,
            'name': name,
            'is_error': None,
            'output_token_estimate': estimate_tool_use_tokens(name, args),
            'input_token_estimate': None,
            'image_token_estimate': None,
            'source_prompt_id': _str(pd.get('conversationRequestId')) or self.current_turn_key,
        }
        step.tool_calls.append(call)
        self.calls[call_id] = _Call(self._key(rec), call, args, rec.get('timestamp'),
                                    step.uuid)
        self.call_order.append(call_id)

    def _on_function_result(self, rec: dict) -> None:
        call_id = _str(rec.get('callId'))
        pending = self.calls.get(call_id) if call_id else None
        if pending is None:
            return
        status = _str(rec.get('status')) or 'completed'
        pending.status = status
        pending.output = _output_text(rec.get('output'))
        pending.end_ms = rec.get('timestamp')
        pending.call['is_error'] = status != 'completed'
        if (isinstance(pending.start_ms, (int, float))
                and isinstance(pending.end_ms, (int, float))):
            pending.call['duration_ms'] = max(0, int(pending.end_ms - pending.start_ms))

    def _on_title(self, rec: dict) -> None:
        title = _str(rec.get('aiTitle'))
        if title:
            self.title = title.strip()


_HANDLERS = {
    'message': _Scan._on_message,
    'reasoning': _Scan._on_reasoning,
    'function_call': _Scan._on_function_call,
    'function_call_result': _Scan._on_function_result,
    'ai-title': _Scan._on_title,
}


def _turn(step: _Step, max_text_bytes: int | None) -> TurnUsage:
    u = step.usage or {}
    cached = _detail_sum(u.get('inputTokensDetails'), 'cached_tokens')
    reasoning = _detail_sum(u.get('outputTokensDetails'), 'reasoning_tokens')
    text, text_trunc = _cap(''.join(step.text), max_text_bytes)
    think, think_trunc = _cap(''.join(step.think), max_text_bytes)
    return TurnUsage(
        model=step.model,
        # `inputTokens` counts the cached prefix too; regin's input_tokens is
        # the uncached part, so input + cache_read is the prompt total.
        input_tokens=max(0, int(u.get('inputTokens') or 0) - cached),
        output_tokens=int(u.get('outputTokens') or 0),
        cache_read_tokens=cached,
        cache_creation_tokens=0,
        reasoning_tokens=reasoning if step.usage is not None else None,
        uuid=step.uuid,
        timestamp=_iso(step.ms),
        message_id=step.message_id,
        text=text,
        text_truncated=text_trunc,
        thinking_text=think,
        thinking_text_truncated=think_trunc,
        thinking_blocks=len(step.think),
        prompt_uuid=step.prompt_uuid,
        tool_calls=tuple(step.tool_calls),
    )


def _tool_attrs(c: _Call) -> dict:
    name = c.call['name']
    attrs: dict = {
        'tool_name': name,
        'tool_use_id': c.call['id'],
        'turn_uuid': c.turn_uuid,
        'transcript_source': True,
    }
    if c.call.get('source_prompt_id'):
        attrs['source_prompt_id'] = c.call['source_prompt_id']
    summary = summarize_tool_input(c.args, command_cap=2000)
    if summary:
        attrs.update(summary)
        attrs['tool_input'] = summary
    return attrs


def _tool_span(c: _Call) -> dict:
    """A ``tool.<name>`` span for one function_call. Paired with a result it
    is terminal (OK / ERROR) and names the PENDING row it supersedes; without
    one it IS that PENDING row, under the reserved prefix the aggregate
    counters skip. The store keeps both; the serve-time merge drops the
    pending once the resolved row exists."""
    attrs = _tool_attrs(c)
    pending_id = f'{TOOL_PENDING_PREFIX}{c.key}'
    start = _iso(c.start_ms)
    if c.status is None:
        return {'span_id': pending_id, 'name': f"tool.{c.call['name']}",
                'status_code': 'PENDING', 'start_time': start, 'end_time': None,
                'duration_ms': 0, 'turn_uuid': c.turn_uuid, 'attributes': attrs}
    attrs['pending_span_id'] = pending_id
    attrs['status'] = c.status
    if c.output:
        out, truncated = _truncate_utf8(c.output, _TOOL_OUTPUT_CAP)
        attrs['stdout' if c.call['name'] == 'Bash' else 'output'] = out
        if truncated:
            attrs['output_truncated'] = True
    if c.status != 'completed':
        attrs['error'] = c.output[:2000] if c.output else c.status
    return {'span_id': f'{TOOL_SPAN_PREFIX}{c.key}', 'name': f"tool.{c.call['name']}",
            'status_code': 'ERROR' if c.status != 'completed' else 'OK',
            'start_time': start, 'end_time': _iso(c.end_ms) or start,
            'duration_ms': int(c.call.get('duration_ms') or 0),
            'turn_uuid': c.turn_uuid, 'attributes': attrs}


def _iter_records(path: str):
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(rec, dict):
                yield rec


def transcript_session_id(path: str) -> str:
    for rec in _iter_records(path):
        sid = _str(rec.get('sessionId'))
        if sid:
            return sid
    return Path(path).stem


def read_usage_codebuddy(path: str, *, max_text_bytes: int | None = None
                         ) -> TranscriptUsage | None:
    """Parse a CodeBuddy-family ``<sessionId>.jsonl``. None on I/O error or
    a transcript with neither a prompt nor an inference."""
    cap = _DEFAULT_TEXT_CAP if max_text_bytes is None else max_text_bytes
    try:
        scan = _Scan(transcript_session_id(path))
        for rec in _iter_records(path):
            scan.feed(rec)
    except OSError:
        return None
    turns = [_turn(scan.steps[g], cap) for g in scan.order]
    if not turns and not scan.prompts:
        return None
    return _build_usage(scan, turns)


def _prompt_maps(prompts) -> tuple[dict, dict, dict]:
    texts: dict[str, str] = {}
    stamps: dict[str, str] = {}
    ids: dict[str, str] = {}
    for key, text, ts, turn_key in prompts:
        if ts:
            stamps[key] = ts
        if text:
            texts[key] = text
            ids[key] = turn_key
    return texts, stamps, ids


def _latest_model(turns: list[TurnUsage]) -> str | None:
    for t in reversed(turns):
        if t.model:
            return t.model
    return None


def _build_usage(scan: _Scan, turns: list[TurnUsage]) -> TranscriptUsage:
    texts, stamps, ids = _prompt_maps(scan.prompts)
    calls = [scan.calls[cid] for cid in scan.call_order]
    return TranscriptUsage(
        turns=turns,
        model=_latest_model(turns),
        input_tokens=sum(t.input_tokens for t in turns),
        output_tokens=sum(t.output_tokens for t in turns),
        cache_read_tokens=sum(t.cache_read_tokens for t in turns),
        cache_creation_tokens=0,
        peak_context_tokens=max((t.context_used for t in turns), default=0),
        prompt_texts=texts,
        prompt_timestamps=stamps,
        prompt_ids=ids,
        tool_use_to_turn_uuid={c.call['id']: c.turn_uuid for c in calls},
        tool_spans=tuple(_tool_span(c) for c in calls if c.key),
        title=scan.title,
    )
