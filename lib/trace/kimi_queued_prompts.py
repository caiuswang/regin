"""Queued / consumed prompts read from a Kimi ``wire.jsonl``.

The Claude-format reader replays `queue-operation` transcript entries; a Kimi
wire has none. Two protocol shapes carry the same facts:

* 1.4 — `turn.steer` (``origin.kind=user``) is the mid-turn input. The wire
  logs nothing when the steer is consumed, so consumption is read off the
  stream: a steer with a ``step.begin`` after it was answered; one with none
  is still queued.
* 1.5 — every typed input gets a stable ``promptId``: an
  ``agent.message.appended`` (``meta.source='input'``, user origin,
  ``tracked``) and/or a ``turn.prompt`` record it, ``agent.turn.started``
  names it as the ``queueItemId`` when its turn begins, and
  ``prompt.completed`` / ``prompt.aborted`` settles it. Queued = recorded
  but never started — identifier-based, no clocks. Any of those record
  types marks the wire 1.5; a 1.4 wire has none of them.

Skill activations and background-task notifications are not user input and
are ignored by both shapes.
"""

from __future__ import annotations

import json

from lib.trace.queued_prompts import _norm

_LIFECYCLE_TYPES = frozenset({
    'agent.turn.started', 'prompt.completed', 'prompt.aborted',
    'agent.message.appended',
})


def _iso(ms: object) -> str | None:
    from lib.trace.kimi_transcript import _iso as iso
    return iso(ms)


def _text(parts: object) -> str:
    if not isinstance(parts, list):
        return ''
    return ''.join(
        str(p.get('text', '')) for p in parts
        if isinstance(p, dict) and p.get('type') == 'text'
    ).strip()


def _scan(path: str) -> dict:
    """Single pass over the wire, collecting both protocols' bookkeeping."""
    out = {
        'lifecycle': False,
        'inputs': {},          # 1.5 promptId -> {text, ts}
        'started': set(),      # 1.5 queueItemIds whose turn began
        'completed': set(),    # 1.5 promptIds settled complete
        'aborted': set(),      # 1.5 promptIds dropped before running
        'steers_14': [],       # [(index, text)] user turn.steer records
        'prompts_14': [],      # [text] user turn.prompt bodies
        'step_positions': [],  # [index] step.begin records
    }
    try:
        with open(path) as fh:
            for idx, line in enumerate(fh):
                rec = _load(line)
                if rec is None:
                    continue
                rtype = rec.get('type')
                if rtype in _LIFECYCLE_TYPES:
                    out['lifecycle'] = True
                handler = _RECORD_HANDLERS.get(rtype)
                if handler is not None:
                    handler(out, idx, rec)
    except (OSError, UnicodeDecodeError):
        out['inputs'] = {}
        out['steers_14'] = []
        out['prompts_14'] = []
        out['step_positions'] = []
    return out


def _load(line: str) -> dict | None:
    line = line.strip()
    if not line:
        return None
    try:
        rec = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None
    return rec if isinstance(rec, dict) else None


def _h_turn_started(out: dict, _idx: int, rec: dict) -> None:
    qid = rec.get('queueItemId')
    if isinstance(qid, str) and qid:
        out['started'].add(qid)


def _h_completed(out: dict, _idx: int, rec: dict) -> None:
    pid = rec.get('promptId')
    if isinstance(pid, str) and pid:
        out['completed'].add(pid)


def _h_aborted(out: dict, _idx: int, rec: dict) -> None:
    pid = rec.get('promptId')
    if isinstance(pid, str) and pid:
        out['aborted'].add(pid)


def _h_step_begin(out: dict, idx: int, _rec: dict) -> None:
    out['step_positions'].append(idx)


def _collect_15_input(out: dict, _idx: int, rec: dict) -> None:
    from lib.trace.kimi_transcript import _tracked_user_input
    found = _tracked_user_input(rec.get('message'))
    if found is None:
        return
    pid, text = found
    out['inputs'].setdefault(pid, {'text': text, 'ts': _iso(rec.get('time'))})


def _user_origin(rec: dict) -> bool:
    origin = rec.get('origin')
    return not isinstance(origin, dict) or origin.get('kind') in (None, 'user')


def _collect_prompt(out: dict, _idx: int, rec: dict) -> None:
    if not _user_origin(rec):
        return
    pid = rec.get('promptId')
    text = _text(rec.get('input'))
    if not text:
        return
    if isinstance(pid, str) and pid:
        # A promptId marks a 1.5 input (1.4 turn.prompt records never carry
        # one): a turn.prompt is the input record when no
        # agent.message.appended preceded it; both name the same promptId.
        out['inputs'].setdefault(
            pid, {'text': text, 'ts': _iso(rec.get('time'))})
    else:
        out['prompts_14'].append(text)


def _collect_steer(out: dict, idx: int, rec: dict) -> None:
    if not _user_origin(rec):
        return
    text = _text(rec.get('input'))
    if text:
        out['steers_14'].append((idx, text))


def _dispatch_loop_event(out: dict, idx: int, rec: dict) -> None:
    event = rec.get('event') or {}
    if event.get('type') == 'step.begin':
        _h_step_begin(out, idx, rec)


_RECORD_HANDLERS = {
    'agent.turn.started': _h_turn_started,
    'prompt.completed': _h_completed,
    'prompt.aborted': _h_aborted,
    'agent.message.appended': _collect_15_input,
    'turn.prompt': _collect_prompt,
    'turn.steer': _collect_steer,
    'context.append_loop_event': _dispatch_loop_event,
}


def current_queued_prompts(path: str) -> list[dict]:
    """`[{content, enqueued_at}]` still queued, oldest first."""
    scan = _scan(path)
    out: list[dict] = []
    if scan['lifecycle']:
        for pid, item in scan['inputs'].items():
            if pid in scan['started'] or pid in scan['aborted']:
                continue
            out.append({'content': _norm(item['text']),
                        'enqueued_at': item['ts']})
    else:
        positions = scan['step_positions']
        for idx, text in scan['steers_14']:
            if not any(p > idx for p in positions):
                out.append({'content': _norm(text), 'enqueued_at': None})
    return out


def consumed_prompt_texts(path: str) -> set[str]:
    """Normalized bodies the wire shows as processed."""
    scan = _scan(path)
    consumed = {_norm(t) for t in scan['prompts_14']}
    if scan['lifecycle']:
        for pid, item in scan['inputs'].items():
            if pid in scan['completed']:
                consumed.add(_norm(item['text']))
    else:
        positions = scan['step_positions']
        for idx, text in scan['steers_14']:
            if any(p > idx for p in positions):
                consumed.add(_norm(text))
    return consumed
