"""Queued / consumed prompt derivation for Kimi wire.jsonl transcripts.

The Claude-format reader replays `queue-operation` entries; a Kimi wire has
none (1.4: `turn.steer` records; 1.5: a promptId lifecycle). These tests pin
the Kimi reader both protocols speak: what is still queued, and what the
transcript has already processed.
"""

from __future__ import annotations

import json

import lib.trace.queued_prompts as qp


def _write(path, recs):
    path.write_text('\n'.join(json.dumps(r) for r in recs) + '\n')


def _loop(event, time=0):
    return {"type": "context.append_loop_event", "event": event, "time": time}


def _started(turn_id, prompt_id, time=0):
    return {"type": "agent.turn.started", "turnId": turn_id,
            "queueItemId": prompt_id, "time": time}


def _ended(turn_id, time=0):
    return {"type": "turn.ended", "turnId": turn_id, "time": time}


def _completed(prompt_id, time=0):
    return {"type": "prompt.completed", "promptId": prompt_id, "time": time}


def _appended_input(prompt_id, text, time=0, tracked=True, in_turn=False):
    origin = {"kind": "user"}
    if in_turn:
        origin["inTurn"] = True
    meta = {"source": "input", "promptId": prompt_id, "origin": origin}
    if tracked:
        meta["tracked"] = True
    return {"type": "agent.message.appended", "kind": "event", "time": time,
            "message": {"message": {"role": "user",
                                    "content": [{"type": "text", "text": text}]},
                        "meta": meta}}


def _prompt(prompt_id, turn_id, text, kind="user", time=0):
    return {"type": "turn.prompt", "promptId": prompt_id, "turnId": turn_id,
            "time": time, "origin": {"kind": kind},
            "input": [{"type": "text", "text": text}]}


class _KimiProvider:
    transcript_format = "kimi"


def _patch(tmp_path, monkeypatch, recs):
    wire = tmp_path / 'wire.jsonl'
    _write(wire, recs)
    monkeypatch.setattr('lib.trace.live_rescan._find_main_transcript',
                        lambda tid: str(wire))
    monkeypatch.setattr('lib.trace.live_rescan._session_provider',
                        lambda tid: _KimiProvider())


def test_15_queued_input_without_turn_start_surfaces(tmp_path, monkeypatch):
    recs = [
        {"type": "metadata", "protocol_version": "1.5"},
        _started(0, "msg-1", time=1),
        _prompt("msg-1", 0, "go", time=1),
        _loop({"type": "step.begin", "uuid": "s1"}),
        _appended_input("msg-2", "follow up please", time=2),
        _loop({"type": "step.end", "uuid": "s1", "usage": {"output": 1}}),
        _ended(0, time=3),
        _completed("msg-1", time=3),
    ]
    _patch(tmp_path, monkeypatch, recs)
    out = qp.current_queued_prompts('t')
    assert [q['content'] for q in out] == ["follow up please"]


def test_15_started_prompt_is_no_longer_queued(tmp_path, monkeypatch):
    recs = [
        {"type": "metadata", "protocol_version": "1.5"},
        _started(0, "msg-1", time=1),
        _prompt("msg-1", 0, "go", time=1),
        _loop({"type": "step.begin", "uuid": "s1"}),
        _appended_input("msg-2", "follow up please", time=2),
        _loop({"type": "step.end", "uuid": "s1", "usage": {"output": 1}}),
        _ended(0, time=3),
        _completed("msg-1", time=3),
        _started(1, "msg-2", time=4),
        _prompt("msg-2", 1, "follow up please", time=4),
        _loop({"type": "step.begin", "uuid": "s2"}),
        _loop({"type": "step.end", "uuid": "s2", "usage": {"output": 1}}),
    ]
    _patch(tmp_path, monkeypatch, recs)
    assert qp.current_queued_prompts('t') == []


def test_15_completed_prompt_counts_as_consumed(tmp_path, monkeypatch):
    recs = [
        {"type": "metadata", "protocol_version": "1.5"},
        _started(0, "msg-1", time=1),
        _prompt("msg-1", 0, "go", time=1),
        _loop({"type": "step.begin", "uuid": "s1"}),
        _appended_input("msg-2", "still waiting", time=2),
        _loop({"type": "step.end", "uuid": "s1", "usage": {"output": 1}}),
        _ended(0, time=3),
        _completed("msg-1", time=3),
    ]
    _patch(tmp_path, monkeypatch, recs)
    assert qp.consumed_prompt_texts('t') == {"go"}


def test_15_skill_activation_is_not_queued_or_consumed(tmp_path, monkeypatch):
    recs = [
        {"type": "metadata", "protocol_version": "1.5"},
        _started(0, "msg-act", time=1),
        _prompt("msg-act", 0, "User activated the skill ...",
                kind="skill_activation", time=1),
        _loop({"type": "step.begin", "uuid": "s1"}),
        _appended_input("msg-1", "typed input", time=2),
        _loop({"type": "step.end", "uuid": "s1", "usage": {"output": 1}}),
        _ended(0, time=3),
    ]
    _patch(tmp_path, monkeypatch, recs)
    assert [q['content'] for q in qp.current_queued_prompts('t')] \
        == ["typed input"]
    assert qp.consumed_prompt_texts('t') == set()


def test_14_unconsumed_steer_surfaces_and_consumed_one_does_not(
        tmp_path, monkeypatch):
    # 1.4: turn.steer records, no promptId lifecycle. A steer the session
    # never answered (no step begins after it) is still queued; one with
    # steps after it was consumed.
    recs = [
        {"type": "metadata", "protocol_version": "1.4"},
        {"type": "turn.prompt", "time": 1, "origin": {"kind": "user"},
         "input": [{"type": "text", "text": "go"}]},
        _loop({"type": "step.begin", "uuid": "s1"}, time=2),
        {"type": "turn.steer", "time": 3, "origin": {"kind": "user"},
         "input": [{"type": "text", "text": "answered steer"}]},
        _loop({"type": "step.begin", "uuid": "s2"}, time=4),
        _loop({"type": "step.end", "uuid": "s2", "usage": {"output": 1}},
              time=5),
        {"type": "turn.steer", "time": 6, "origin": {"kind": "user"},
         "input": [{"type": "text", "text": "orphaned steer"}]},
    ]
    _patch(tmp_path, monkeypatch, recs)
    assert [q['content'] for q in qp.current_queued_prompts('t')] \
        == ["orphaned steer"]
    assert "answered steer" in qp.consumed_prompt_texts('t')
    assert "orphaned steer" not in qp.consumed_prompt_texts('t')


def test_14_background_task_steer_is_ignored(tmp_path, monkeypatch):
    recs = [
        {"type": "metadata", "protocol_version": "1.4"},
        {"type": "turn.prompt", "time": 1, "origin": {"kind": "user"},
         "input": [{"type": "text", "text": "go"}]},
        _loop({"type": "step.begin", "uuid": "s1"}, time=2),
        {"type": "turn.steer", "time": 3,
         "origin": {"kind": "background_task", "taskId": "bash-1"},
         "input": [{"type": "text", "text": "<notification>done</notification>"}]},
    ]
    _patch(tmp_path, monkeypatch, recs)
    assert qp.current_queued_prompts('t') == []


def test_15_untracked_tool_feed_is_not_queued(tmp_path, monkeypatch):
    recs = [
        {"type": "metadata", "protocol_version": "1.5"},
        _started(0, "msg-1", time=1),
        _prompt("msg-1", 0, "go", time=1),
        _loop({"type": "step.begin", "uuid": "s1"}),
        _appended_input("msg-skill", "Skill tool loaded instructions ...",
                        time=2, tracked=False, in_turn=True),
        _loop({"type": "step.end", "uuid": "s1", "usage": {"output": 1}}),
        _ended(0, time=3),
        _completed("msg-1", time=3),
    ]
    _patch(tmp_path, monkeypatch, recs)
    assert qp.current_queued_prompts('t') == []
    assert qp.consumed_prompt_texts('t') == {"go"}
