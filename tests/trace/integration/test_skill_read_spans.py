"""Skill read scenario (7).

`skill_read_trace_hook.py` emits a `skill.read` span when Claude's Read tool
targets a path matching `.claude/skills/<id>/content.md` (either relative or
under $HOME). We test this by asking Claude to Read a known user-level skill
file explicitly — a plain `/skill-name` invocation may be served from cache
without a Read tool call.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def _find_user_skill_content() -> Path | None:
    skills_dir = Path.home() / ".claude" / "skills"
    if not skills_dir.is_dir():
        return None
    for entry in sorted(skills_dir.iterdir()):
        content = entry / "content.md"
        if content.is_file():
            return content
    return None


def test_reading_skill_content_emits_skill_read(trace_session):
    skill_file = _find_user_skill_content()
    if not skill_file:
        pytest.skip("no user-level skill with content.md available to read")

    trace_session.send(
        f"use the Read tool to read the file at {skill_file} "
        f"and tell me its first line",
        idle_timeout=120,
    )

    reads = trace_session.assert_span("skill.read", min_count=1)
    skill_ids = [(s.get("attributes") or {}).get("skill_id") for s in reads]
    expected_id = skill_file.parent.name
    assert expected_id in skill_ids, (
        f"expected skill.read with skill_id={expected_id!r}; got {skill_ids}"
    )
