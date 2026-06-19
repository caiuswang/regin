"""End-to-end wiring for the resumable ingest path.

Every `_do_rescan` test mocks the ingest wrappers, and `_do_rescan` swallows
exceptions — so a kwarg/signature break between `live_rescan` and the resumable
entry points would fail silently (live updates just stop). These run the REAL
`ingest_transcript_usage_resumable` / `emit_subagent_responses_resumable`
through to `post_span`, asserting the expected spans are posted.
"""

from __future__ import annotations

import json


def _write(tmp_path, name, *entries) -> str:
    p = tmp_path / name
    with open(p, "w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")
    return str(p)


def _assistant_with_text(uuid, parent, ts, msg_id, text):
    return {
        "type": "assistant", "uuid": uuid, "parentUuid": parent, "timestamp": ts,
        "message": {
            "id": msg_id, "model": "claude-opus-4-7",
            "usage": {"input_tokens": 10, "output_tokens": 5,
                      "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
            "content": [{"type": "text", "text": text}],
        },
    }


def _patch_hook_io(monkeypatch):
    """Patch post_span (collect) + post_event (no-op) so no network call fires
    and we can see exactly which spans the real ingest path posts."""
    posted: list = []

    def _fake_post_span(*a, **k):
        posted.append((k.get("span_id"), k.get("name")))
        return True

    monkeypatch.setattr("lib.hook_plugin.post_span", _fake_post_span)
    monkeypatch.setattr("lib.hook_plugin.post_event", lambda *a, **k: True)
    return posted


def test_ingest_resumable_posts_prompt_and_response_spans(tmp_path, monkeypatch):
    monkeypatch.setenv("REGIN_TURN_TRACE_STATE_DIR", str(tmp_path / "state"))
    posted = _patch_hook_io(monkeypatch)
    path = _write(
        tmp_path, "main.jsonl",
        {"type": "user", "uuid": "p0", "parentUuid": None,
         "timestamp": "2026-05-20T10:00:00Z", "message": {"content": "hello there"}},
        _assistant_with_text("a0", "p0", "2026-05-20T10:00:05Z", "m0", "hi back"),
    )
    from hook_manager.handlers.turn_trace.entry import (
        ingest_transcript_usage_resumable,
    )
    state = ingest_transcript_usage_resumable("trace-1", path, None)
    span_ids = [s for s, _ in posted]
    assert any(s.startswith("prompt-") for s in span_ids), span_ids
    assert any(s.startswith("resp-") for s in span_ids), span_ids
    # state threads back for the next poll
    assert state is not None and state.offset > 0


def test_ingest_resumable_posts_recap_span_for_away_summary(tmp_path, monkeypatch):
    """A `system: away_summary` recap entry becomes a `harness.recap`
    span (id `sys-<uuid[:13]>`), carrying its prose content."""
    monkeypatch.setenv("REGIN_TURN_TRACE_STATE_DIR", str(tmp_path / "state"))
    posted: list = []

    def _fake_post_span(*a, **k):
        posted.append((k.get("span_id"), k.get("name"), k.get("attributes")))
        return True

    monkeypatch.setattr("lib.hook_plugin.post_span", _fake_post_span)
    monkeypatch.setattr("lib.hook_plugin.post_event", lambda *a, **k: True)
    path = _write(
        tmp_path, "main.jsonl",
        {"type": "user", "uuid": "p0", "parentUuid": None,
         "timestamp": "2026-05-20T10:00:00Z", "message": {"content": "hello"}},
        _assistant_with_text("a0", "p0", "2026-05-20T10:00:05Z", "m0", "hi back"),
        {"type": "system", "subtype": "away_summary", "uuid": "recap0",
         "parentUuid": "a0", "timestamp": "2026-05-20T10:05:00Z",
         "content": "Recap of the work so far."},
    )
    from hook_manager.handlers.turn_trace.entry import (
        ingest_transcript_usage_resumable,
    )
    ingest_transcript_usage_resumable("trace-1", path, None)
    recap = next((p for p in posted if p[1] == "harness.recap"), None)
    assert recap is not None, posted
    span_id, _, attrs = recap
    assert span_id == "sys-recap0"
    assert attrs.get("subtype") == "away_summary"
    assert attrs.get("content") == "Recap of the work so far."
    assert attrs.get("content_truncated") is False


def test_ingest_resumable_second_poll_parses_only_appended_bytes(tmp_path, monkeypatch):
    monkeypatch.setenv("REGIN_TURN_TRACE_STATE_DIR", str(tmp_path / "state"))
    _patch_hook_io(monkeypatch)
    path = _write(
        tmp_path, "main.jsonl",
        {"type": "user", "uuid": "p0", "parentUuid": None,
         "timestamp": "2026-05-20T10:00:00Z", "message": {"content": "hello"}},
        _assistant_with_text("a0", "p0", "2026-05-20T10:00:05Z", "m0", "first"),
    )
    from hook_manager.handlers.turn_trace.entry import (
        ingest_transcript_usage_resumable,
    )
    state = ingest_transcript_usage_resumable("trace-1", path, None)
    first_offset = state.offset
    # append a second turn; the next poll must advance the committed offset
    with open(path, "a") as f:
        f.write(json.dumps(
            _assistant_with_text("a1", "a0", "2026-05-20T10:01:05Z", "m1", "second")
        ) + "\n")
    state = ingest_transcript_usage_resumable("trace-1", path, state)
    assert state.offset > first_offset


def test_emit_subagent_resumable_posts_response_spans(tmp_path, monkeypatch):
    monkeypatch.setenv("REGIN_TURN_TRACE_STATE_DIR", str(tmp_path / "state"))
    posted = _patch_hook_io(monkeypatch)
    path = _write(
        tmp_path, "agent-abc.jsonl",
        {"type": "user", "uuid": "sp0", "parentUuid": None,
         "timestamp": "2026-05-20T10:00:00Z", "message": {"content": "subagent task"}},
        _assistant_with_text("sa0", "sp0", "2026-05-20T10:00:05Z", "sm0", "subagent reply"),
    )
    from hook_manager.handlers.subagent_lifecycle import (
        emit_subagent_responses_resumable,
    )
    state = emit_subagent_responses_resumable(
        "trace-1", path, "abc", None, seen=set(),
    )
    span_ids = [s for s, _ in posted]
    assert any(s.startswith("resp-sa-") for s in span_ids), span_ids
    assert state is not None and state.offset > 0
