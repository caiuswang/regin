"""Deferred-injection queue for PostToolUse rule findings on Kimi.

Kimi Code 0.29.1 reads hook stdout back into the model context only on
UserPromptSubmit — measured over the local wire corpus, 37/37 `hook_result`
context entries carry `event == "UserPromptSubmit"` and none carry a tool
event. A rule violation found on PostToolUse is therefore parked and replayed
on the session's next prompt. Claude reads PostToolUse `additionalContext`
inline and must never touch the queue.
"""

from __future__ import annotations

import json
from typing import List
from unittest import mock

import pytest

from hook_manager.core import HookPayload, HookResponse
from hook_manager.handlers import rule_check
from hook_manager.merge import kimi_response_text, merge_responses, response_to_json
from lib.rule_engines.base import Rule, Violation


class _ViolatingEngine:
    kind = "fake"
    id = "fake"
    language_ids = ("python",)
    project_root = None

    def __init__(self, rules: List[Rule]):
        self._rules = rules

    def parse_rules(self):
        return list(self._rules)

    def applies_to(self, rule, file_path, content):
        return True

    def applicable_rules(self, file_path, content):
        from lib.rule_engines.base import default_applicable_rules
        return default_applicable_rules(self, file_path, content)

    def run(self, rule, file_path, repo_root):
        return Violation(rule_id=rule.id, file_path=file_path, match_count=1,
                         detail="bare except at line 12")


@pytest.fixture
def violating_engine(monkeypatch):
    rule = Rule(
        id="R_BARE_EXCEPT", engine="fake", summary="no bare except",
        severity="error", triggers=("*.py",), source_file="R.fake", metadata={},
    )
    from lib import rule_engines as re_pkg
    monkeypatch.setattr(re_pkg, "all_engines", lambda: [_ViolatingEngine([rule])])
    monkeypatch.setattr(
        "lib.rules.engine_rule_disable.disabled_ids", lambda _eid: set()
    )


def _edit_payload(session_id: str, target, *, kimi: bool) -> HookPayload:
    raw = {"agent_type": "kimi"} if kimi else {}
    return HookPayload(
        event="PostToolUse",
        tool_name="Edit",
        cwd=str(target.parent),
        tool_input={"file_path": str(target)},
        tool_response={"filePath": str(target)},
        session_id=session_id,
        raw=raw,
    )


def _prompt_payload(session_id: str, *, kimi: bool) -> HookPayload:
    return HookPayload(
        event="UserPromptSubmit",
        prompt="carry on",
        session_id=session_id,
        raw={"agent_type": "kimi"} if kimi else {},
    )


def _run_post_tool(payload) -> HookResponse | None:
    with mock.patch.object(rule_check, "_emit_rule_check_span", lambda *a, **k: "sp1"), \
            mock.patch("lib.hook_plugin.post_event", lambda *a, **k: None):
        return rule_check.handle(payload)


@pytest.fixture
def py_file(tmp_path):
    target = tmp_path / "code.py"
    target.write_text("try:\n    x = 1\nexcept:\n    pass\n")
    return target


# ── Kimi: enqueue on PostToolUse, drain on the next prompt ────────────


def test_kimi_violation_enqueues_and_injects_nothing_inline(
    tmp_db, tmp_config_dir, py_file, violating_engine,
):
    resp = _run_post_tool(_edit_payload("kimi-1", py_file, kimi=True))
    assert resp is not None

    # Nothing reaches Kimi on the PostToolUse wire — that stdout is discarded.
    assert kimi_response_text("PostToolUse", merge_responses([resp])) == ""

    parked = rule_check._read_pending(rule_check._pending_path("kimi-1"))
    assert len(parked) == 1
    assert "R_BARE_EXCEPT" in parked[0]


def test_next_prompt_drains_queue_into_additional_context(
    tmp_db, tmp_config_dir, py_file, violating_engine,
):
    _run_post_tool(_edit_payload("kimi-1", py_file, kimi=True))

    drained = rule_check.handle_prompt(_prompt_payload("kimi-1", kimi=True))
    assert drained is not None
    assert "R_BARE_EXCEPT" in drained.additional_context
    # UserPromptSubmit is the one event Kimi reads back.
    assert "R_BARE_EXCEPT" in kimi_response_text(
        "UserPromptSubmit", merge_responses([drained]),
    )


def test_second_drain_injects_nothing(
    tmp_db, tmp_config_dir, py_file, violating_engine,
):
    _run_post_tool(_edit_payload("kimi-1", py_file, kimi=True))
    assert rule_check.handle_prompt(_prompt_payload("kimi-1", kimi=True)) is not None
    assert rule_check.handle_prompt(_prompt_payload("kimi-1", kimi=True)) is None


def test_queue_is_per_session(tmp_db, tmp_config_dir, py_file, violating_engine):
    _run_post_tool(_edit_payload("kimi-1", py_file, kimi=True))
    assert rule_check.handle_prompt(_prompt_payload("kimi-2", kimi=True)) is None
    assert rule_check.handle_prompt(_prompt_payload("kimi-1", kimi=True)) is not None


def test_drain_is_silent_when_nothing_parked(tmp_db, tmp_config_dir):
    assert rule_check.handle_prompt(_prompt_payload("kimi-cold", kimi=True)) is None


# ── Bounds ────────────────────────────────────────────────────────────


def test_queue_drops_oldest_beyond_the_cap(tmp_config_dir):
    for i in range(rule_check._MAX_PENDING_FINDINGS + 3):
        rule_check._enqueue_pending("kimi-1", f"finding {i}")
    parked = rule_check._read_pending(rule_check._pending_path("kimi-1"))
    assert len(parked) == rule_check._MAX_PENDING_FINDINGS
    assert parked[0] == "finding 3"
    assert parked[-1] == f"finding {rule_check._MAX_PENDING_FINDINGS + 2}"


def test_each_finding_is_size_capped(tmp_config_dir):
    rule_check._enqueue_pending("kimi-1", "x" * 50_000)
    parked = rule_check._read_pending(rule_check._pending_path("kimi-1"))
    assert len(parked[0]) == rule_check._MAX_FINDING_CHARS


def test_multiline_findings_survive_the_round_trip(tmp_config_dir):
    body = "rule-check: 1 violation\n- `R` (error): no bare except\n\nFix it."
    rule_check._enqueue_pending("kimi-1", body)
    assert rule_check._drain_pending("kimi-1") == [body]


def test_stale_queue_files_are_expired(tmp_config_dir):
    import os
    import time

    rule_check._enqueue_pending("kimi-abandoned", "old finding")
    stale = rule_check._pending_path("kimi-abandoned")
    aged = time.time() - rule_check._PENDING_MAX_AGE_SEC - 60
    os.utime(stale, (aged, aged))

    rule_check._enqueue_pending("kimi-live", "new finding")

    assert not stale.exists()
    assert rule_check._read_pending(rule_check._pending_path("kimi-live")) == [
        "new finding",
    ]


def test_session_id_cannot_escape_the_queue_dir(tmp_config_dir):
    path = rule_check._pending_path("../../etc/passwd")
    assert path.parent == rule_check._pending_path("x").parent
    assert rule_check._pending_path(None) is None
    assert rule_check._pending_path("") is None


# ── Claude parity: inline, immediate, no queue ────────────────────────


def test_claude_violation_stays_inline_and_never_queues(
    tmp_db, tmp_config_dir, py_file, violating_engine,
):
    resp = _run_post_tool(_edit_payload("claude-1", py_file, kimi=False))
    assert resp is not None
    assert "R_BARE_EXCEPT" in resp.additional_context
    assert response_to_json("PostToolUse", merge_responses([resp])) == {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": resp.additional_context,
        },
    }
    assert rule_check._read_pending(rule_check._pending_path("claude-1")) == []
    assert not rule_check._pending_path("claude-1").parent.exists()


# ── End-to-end through the runner ─────────────────────────────────────


def _registry():
    from hook_manager.core import Handler
    return [
        Handler(name='rule_check', events=['PostToolUse'], kind='enrich',
                priority=120, fn=rule_check.handle),
        Handler(name='rule_check_prompt', events=['UserPromptSubmit'],
                kind='enrich', priority=90, fn=rule_check.handle_prompt),
    ]


def _run_hook(event: str, payload: dict) -> tuple[str, int]:
    import io

    from hook_manager import runner

    out = io.StringIO()
    with mock.patch.object(rule_check, '_emit_rule_check_span', lambda *a, **k: 'sp'), \
            mock.patch('lib.hook_plugin.post_event', lambda *a, **k: None), \
            mock.patch('lib.hook_plugin.set_active_agent_type', lambda *a: None):
        code = runner.run(event, _registry(), json.dumps(payload), out)
    return out.getvalue(), code


def test_kimi_wire_path_end_to_end(tmp_db, tmp_config_dir, py_file, violating_engine):
    """PostToolUse writes nothing to Kimi's stdout; the next prompt carries the
    finding as the plain text Kimi reads back."""
    edit_stdout, edit_code = _run_hook('PostToolUse', {
        'hook_event_name': 'PostToolUse', 'session_id': 'kimi-e2e',
        'agent_type': 'kimi', 'tool_name': 'Edit', 'cwd': str(py_file.parent),
        'tool_input': {'file_path': str(py_file)},
        'tool_response': {'filePath': str(py_file)},
    })
    assert edit_stdout == ''
    assert edit_code == 0

    prompt_stdout, prompt_code = _run_hook('UserPromptSubmit', {
        'hook_event_name': 'UserPromptSubmit', 'session_id': 'kimi-e2e',
        'agent_type': 'kimi', 'prompt': 'keep going',
    })
    assert 'R_BARE_EXCEPT' in prompt_stdout
    assert prompt_code == 0

    # Replaying the same prompt injects nothing a second time.
    assert _run_hook('UserPromptSubmit', {
        'hook_event_name': 'UserPromptSubmit', 'session_id': 'kimi-e2e',
        'agent_type': 'kimi', 'prompt': 'keep going',
    })[0] == ''


def test_claude_wire_path_end_to_end(tmp_db, tmp_config_dir, py_file, violating_engine):
    """Claude still gets the finding inline on PostToolUse, and its prompt
    event stays free of any replay."""
    edit_stdout, _ = _run_hook('PostToolUse', {
        'hook_event_name': 'PostToolUse', 'session_id': 'claude-e2e',
        'tool_name': 'Edit', 'cwd': str(py_file.parent),
        'tool_input': {'file_path': str(py_file)},
        'tool_response': {'filePath': str(py_file)},
    })
    assert json.loads(edit_stdout)['hookSpecificOutput']['hookEventName'] == 'PostToolUse'
    assert 'R_BARE_EXCEPT' in json.loads(edit_stdout)['hookSpecificOutput']['additionalContext']

    prompt_stdout, _ = _run_hook('UserPromptSubmit', {
        'hook_event_name': 'UserPromptSubmit', 'session_id': 'claude-e2e',
        'prompt': 'keep going',
    })
    assert 'additionalContext' not in prompt_stdout
    assert rule_check._read_pending(rule_check._pending_path('claude-e2e')) == []


def test_claude_prompt_never_drains(tmp_db, tmp_config_dir, py_file, violating_engine):
    # Even with a queue file present, a Claude session ignores it.
    rule_check._enqueue_pending("claude-1", "stale finding")
    assert rule_check.handle_prompt(_prompt_payload("claude-1", kimi=False)) is None
    assert rule_check._read_pending(rule_check._pending_path("claude-1")) == [
        "stale finding",
    ]
