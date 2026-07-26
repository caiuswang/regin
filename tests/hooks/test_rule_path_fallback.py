"""Provider-parity for the edited-file path across the PostToolUse handlers.

Kimi's Edit/Write/Read payloads name the file under `tool_input['path']` and
make it repo-relative to `cwd`; Claude sends an absolute `file_path` (plus
`tool_response['filePath']`). rule_check / doc_check / skill_read must resolve
both shapes to the same absolute file, without changing Claude behaviour.
"""

from __future__ import annotations

from typing import List
from unittest import mock

import pytest

from hook_manager.core import HookPayload
from hook_manager.handlers import doc_check, rule_check, skill_read
from lib.rule_engines.base import Rule, Violation


def _kimi_payload(tool_name: str, rel_path: str, cwd: str, **tool_input) -> HookPayload:
    return HookPayload(
        event="PostToolUse",
        tool_name=tool_name,
        cwd=cwd,
        tool_input={"path": rel_path, **tool_input},
        tool_response={"output": "ok"},
        session_id="kimi-session",
        raw={"agent_type": "kimi"},
    )


def _claude_payload(tool_name: str, abs_path: str, cwd: str, **tool_input) -> HookPayload:
    return HookPayload(
        event="PostToolUse",
        tool_name=tool_name,
        cwd=cwd,
        tool_input={"file_path": abs_path, **tool_input},
        tool_response={"filePath": abs_path},
        session_id="claude-session",
    )


# ── rule_check ────────────────────────────────────────────────────────


def test_rule_check_extracts_relative_kimi_path(tmp_path):
    (tmp_path / "web").mkdir()
    target = tmp_path / "web" / "blueprints.py"
    target.write_text("x = 1\n")

    payload = _kimi_payload("Edit", "web/blueprints.py", str(tmp_path))
    assert rule_check._extract_file_path(payload) == str(target)


def test_rule_check_leaves_claude_absolute_path_untouched(tmp_path):
    target = tmp_path / "code.py"
    target.write_text("x = 1\n")

    payload = _claude_payload("Edit", str(target), "/some/other/cwd")
    assert rule_check._extract_file_path(payload) == str(target)


def test_rule_check_notebook_path_is_resolved(tmp_path):
    payload = HookPayload(
        event="PostToolUse",
        tool_name="Edit",
        cwd=str(tmp_path),
        tool_input={"notebook_path": "nb/run.ipynb"},
    )
    assert rule_check._extract_file_path(payload) == str(tmp_path / "nb" / "run.ipynb")


def test_rule_check_without_cwd_keeps_relative_path():
    payload = HookPayload(
        event="PostToolUse", tool_name="Edit", tool_input={"path": "a/b.py"},
    )
    assert rule_check._extract_file_path(payload) == "a/b.py"


class _FakeEngine:
    kind = "fake"
    language_ids = ("python",)
    project_root = None

    def __init__(self, engine_id: str, rules: List[Rule]):
        self.id = engine_id
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
                         detail="boom")


@pytest.fixture
def fake_engine_only(monkeypatch):
    rule = Rule(
        id="R_ANY", engine="fake", summary="rule R_ANY", severity="warn",
        triggers=("*.py",), source_file="R_ANY.fake", metadata={},
    )
    engine = _FakeEngine("fake", [rule])
    from lib import rule_engines as re_pkg
    monkeypatch.setattr(re_pkg, "all_engines", lambda: [engine])
    monkeypatch.setattr(
        "lib.rules.engine_rule_disable.disabled_ids", lambda _eid: set()
    )
    return engine


def _run_handle(payload) -> tuple[object, dict, list]:
    captured: dict = {}
    posted: list = []

    def _capture(*args, **kwargs):
        captured.update(kwargs)
        captured["file_path"] = args[1]
        return "span123"

    with mock.patch.object(rule_check, "_emit_rule_check_span", _capture), \
            mock.patch("lib.hook_plugin.post_event",
                       lambda name, events, **kw: posted.append((name, events))):
        resp = rule_check.handle(payload)
    return resp, captured, posted


def test_rule_check_handle_fires_on_kimi_relative_path(tmp_db, tmp_config_dir,
                                                       tmp_path,
                                                       fake_engine_only):
    (tmp_path / "web").mkdir()
    target = tmp_path / "web" / "blueprints.py"
    target.write_text("x = 1\n")

    resp, captured, posted = _run_handle(
        _kimi_payload("Edit", "web/blueprints.py", str(tmp_path)),
    )

    assert resp is not None
    assert "R_ANY" in resp.additional_context
    assert captured["file_path"] == str(target)
    assert posted and posted[0][0] == "rule_triggers"
    assert posted[0][1][0]["rule_id"] == "R_ANY"


def test_rule_check_handle_unchanged_for_claude(tmp_db, tmp_config_dir, tmp_path,
                                                fake_engine_only):
    target = tmp_path / "code.py"
    target.write_text("x = 1\n")

    resp, captured, posted = _run_handle(
        _claude_payload("Edit", str(target), str(tmp_path)),
    )

    assert resp is not None
    assert captured["file_path"] == str(target)
    assert posted[0][1][0]["rule_id"] == "R_ANY"


def test_rule_check_skips_missing_relative_file(tmp_db, tmp_path, fake_engine_only):
    resp, _captured, _posted = _run_handle(
        _kimi_payload("Edit", "web/nope.py", str(tmp_path)),
    )
    assert resp is None


# ── doc_check ─────────────────────────────────────────────────────────


_ROT_TEXT = "This suite has 12 tests and we plan to add more.\n"


def test_doc_check_fires_on_kimi_relative_path(tmp_path):
    payload = _kimi_payload("Write", "docs/guide.md", str(tmp_path),
                            content=_ROT_TEXT)
    resp = doc_check.handle(payload)
    assert resp is not None
    assert str(tmp_path / "docs" / "guide.md") in resp.additional_context


def test_doc_check_unchanged_for_claude(tmp_path):
    abs_path = str(tmp_path / "docs" / "guide.md")
    payload = _claude_payload("Write", abs_path, str(tmp_path), content=_ROT_TEXT)
    resp = doc_check.handle(payload)
    assert resp is not None
    assert abs_path in resp.additional_context


def test_doc_check_ignores_non_markdown_kimi_path(tmp_path):
    payload = _kimi_payload("Write", "lib/thing.py", str(tmp_path),
                            content=_ROT_TEXT)
    assert doc_check.handle(payload) is None


def test_doc_check_skip_markers_still_apply_to_relative_path(tmp_path):
    payload = _kimi_payload("Write", "node_modules/pkg/README.md", str(tmp_path),
                            content=_ROT_TEXT)
    assert doc_check.handle(payload) is None


# ── skill_read ────────────────────────────────────────────────────────


def _read_payload(tool_input: dict, cwd: str | None) -> HookPayload:
    return HookPayload(
        event="PostToolUse", tool_name="Read", cwd=cwd,
        tool_input=tool_input, session_id="s1",
    )


def test_skill_read_matches_project_relative_kimi_path(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "lib.hook_plugin.skill_id_from_read_path",
        lambda fp, **kw: "demo" if fp == ".claude/skills/demo/content.md" else None,
    )
    emitted: list = []
    monkeypatch.setattr(skill_read, "_emit_span",
                        lambda p, sid, fp: emitted.append((sid, fp)))

    resp = skill_read.handle(
        _read_payload({"path": ".claude/skills/demo/content.md"}, str(tmp_path)),
    )
    assert resp is not None
    assert emitted == [("demo", ".claude/skills/demo/content.md")]


def test_skill_read_absolutizes_when_relative_does_not_match(monkeypatch, tmp_path):
    absolute = str(tmp_path / ".claude" / "skills" / "demo" / "content.md")
    monkeypatch.setattr(
        "lib.hook_plugin.skill_id_from_read_path",
        lambda fp, **kw: "demo" if fp == absolute else None,
    )
    emitted: list = []
    monkeypatch.setattr(skill_read, "_emit_span",
                        lambda p, sid, fp: emitted.append((sid, fp)))

    resp = skill_read.handle(
        _read_payload({"path": ".claude/skills/demo/content.md"}, str(tmp_path)),
    )
    assert resp is not None
    assert emitted == [("demo", absolute)]


def test_skill_read_unchanged_for_claude(monkeypatch, tmp_path):
    absolute = "/home/u/.claude/skills/demo/content.md"
    seen: list = []

    def _lookup(fp, **_kw):
        seen.append(fp)
        return "demo" if fp == absolute else None

    monkeypatch.setattr("lib.hook_plugin.skill_id_from_read_path", _lookup)
    emitted: list = []
    monkeypatch.setattr(skill_read, "_emit_span",
                        lambda p, sid, fp: emitted.append((sid, fp)))

    resp = skill_read.handle(_read_payload({"file_path": absolute}, str(tmp_path)))
    assert resp is not None
    assert emitted == [("demo", absolute)]
    assert seen == [absolute]


def test_skill_read_ignores_ordinary_read(monkeypatch, tmp_path):
    monkeypatch.setattr("lib.hook_plugin.skill_id_from_read_path",
                        lambda fp, **kw: None)
    assert skill_read.handle(
        _read_payload({"path": "web/app.py"}, str(tmp_path)),
    ) is None
