"""Capture wiring: send_to_user(type=lesson) → agent_messages + memory."""

from __future__ import annotations

import lib.memory as memory
from hook_manager.core import HookPayload
from hook_manager.handlers.post_tool_trace import _record_agent_message


def _payload(session_id="sess-lesson", cwd="/tmp/nowhere"):
    return HookPayload(event="PostToolUse",
                       tool_name="mcp__send-to-user__send_to_user",
                       session_id=session_id, cwd=cwd,
                       raw={"session_id": session_id, "cwd": cwd})


def _send(msg_type, message="A reusable lesson body.", title="Lesson title",
          attrs=None, span_id="span-abc", **extra):
    _record_agent_message(
        _payload(), {"message": message, "type": msg_type, "title": title,
                     **extra},
        attrs or {}, span_id)


def test_lesson_lands_in_memory_with_provenance():
    _send("lesson", attrs={"agent_id": "agent-7"})

    rows = memory.get_store().list_memories(include_tests=True)
    assert len(rows) == 1
    mem = rows[0]
    expected = {
        "kind": "lesson",
        "body": "A reusable lesson body.",
        "title": "Lesson title",
        "source_trace_id": "sess-lesson",
        "source_span_id": "span-abc",
        "source_agent_id": "agent-7",
        "tags": ["send_to_user"],          # provenance category
        "is_test": True,                   # REGIN_TRACE_TEST=1 via conftest
    }
    assert {k: mem[k] for k in expected} == expected


def test_lesson_also_lands_in_inbox():
    _send("lesson")
    from lib.agent_messages import store as msg_store
    msgs = msg_store.list_session_messages("sess-lesson")
    assert len(msgs) == 1 and msgs[0]["msg_type"] == "lesson"


def test_non_lesson_does_not_write_memory():
    _send("progress")
    assert memory.get_store().list_memories(include_tests=True) == []


def test_lesson_supersedes_existing_memory():
    """`supersedes` retires the named memory and chains the new lesson onto
    it (status=retired + superseded_by), instead of a fresh insert."""
    old_id = memory.remember("Stale lesson.", kind="lesson", title="stale",
                             tags=["send_to_user"], is_test=True)
    _send("lesson", message="Corrected lesson.", title="fresh",
          supersedes=old_id)

    store = memory.get_store()
    old = store.get_dict(old_id)
    assert old["status"] == "retired"
    new = store.get_dict(old["superseded_by"])
    assert new["status"] == "active" and new["body"] == "Corrected lesson."
    # exactly the replacement is active — the retired original is hidden
    active = [m["id"] for m in store.list_memories(status="active",
                                                   include_tests=True)]
    assert active == [old["superseded_by"]]


def test_lesson_supersedes_unknown_id_falls_back_to_insert():
    """An unresolvable `supersedes` id degrades to a plain insert rather
    than dropping the lesson."""
    _send("lesson", message="Body.", supersedes="does-not-exist")
    rows = memory.get_store().list_memories(include_tests=True)
    assert len(rows) == 1
    assert rows[0]["status"] == "active" and rows[0]["superseded_by"] is None


def test_lesson_skips_memory_when_disabled(monkeypatch):
    from lib.settings import settings
    monkeypatch.setattr(settings.agent_memory, "enabled", False)
    _send("lesson")
    assert memory.get_store().list_memories(include_tests=True) == []
    # the inbox message still landed — only the memory tee is gated
    from lib.agent_messages import store as msg_store
    assert msg_store.list_session_messages("sess-lesson")


def test_lesson_scope_stamped_by_default_policy(monkeypatch, tmp_path):
    """Default policy is per-repo-tagged: writes from a registered repo
    carry its scope without any configuration."""
    from lib.settings import settings
    assert settings.agent_memory.scope_policy == "per-repo-tagged"
    repo = tmp_path / "myrepo"
    repo.mkdir()
    monkeypatch.setattr(settings, "repo_paths", [repo])

    _record_agent_message(
        _payload(cwd=str(repo / "src")),
        {"message": "repo-scoped lesson", "type": "lesson"}, {}, "sp1")
    rows = memory.get_store().list_memories(include_tests=True)
    assert rows[0]["scope"] == "repo:myrepo"

    # ...but recall is NOT narrowed under per-repo-tagged (only per-repo
    # narrows) — repo memories stay globally visible.
    from lib.memory.scoping import resolve_recall_scope
    assert resolve_recall_scope(str(repo / "src")) is None
    monkeypatch.setattr(settings.agent_memory, "scope_policy", "per-repo")
    assert resolve_recall_scope(str(repo / "src")) == "repo:myrepo"


def test_lesson_is_valid_message_type():
    from lib.orm.models.agent_messages import MESSAGE_TYPES, severity_rank
    assert "lesson" in MESSAGE_TYPES
    # below the default webhook gate severity
    assert severity_rank("lesson") < severity_rank("warning")
