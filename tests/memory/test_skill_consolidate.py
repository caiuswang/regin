"""skill_consolidate: graduate proven skill-memories into SKILL.md.

Covers the pure section-append, the promotion bar, the non-manual write +
retire path, and the two preview-only skips (manual / source-less).
"""

from __future__ import annotations

import lib.memory as memory
from lib.memory.skill_consolidate import append_lesson, consolidate_skills

_LEAF = "skill-playwright-screenshots"
_SLUG = "playwright-screenshots"


def _src(text: str = "Guide body.\n", manual: bool = False):
    manual_line = "manual: true\n" if manual else ""
    return (f"---\nname: {_SLUG}\n{manual_line}"
            f'description: "test skill"\n---\n\n{text}')


def _patterns_dir(monkeypatch, tmp_path, *, manual=False, has_source=True):
    from lib.settings import settings
    pdir = tmp_path / "patterns"
    monkeypatch.setattr(settings, "patterns_dir", pdir)
    if has_source:
        (pdir / _SLUG).mkdir(parents=True)
        (pdir / _SLUG / "SKILL.md").write_text(_src(manual=manual))
    return pdir


def _seed_memory(recall_count: int):
    mid = memory.remember(
        "Playwright reuseExistingServer keeps a stale Python on :8321; "
        "restart the backend after edits or E2E asserts against old code.",
        kind="gotcha", title="Restart backend for E2E", is_test=True)
    memory.get_store().link_authoritative_topic(mid, _LEAF, source="manual")
    if recall_count:
        from sqlmodel import select
        from lib.memory.models import Memory
        from lib.memory.engine import MemorySessionLocal
        with MemorySessionLocal() as s:
            row = s.exec(select(Memory).where(Memory.id == mid)).first()
            row.recall_count = recall_count
            s.add(row)
            s.commit()
    return mid


# ── pure section-append ────────────────────────────────────────────────

def test_append_lesson_creates_section_when_absent():
    out = append_lesson("Body.\n", "- a lesson")
    assert "## Lessons (from agent memory)" in out
    assert out.rstrip().endswith("- a lesson")


def test_append_lesson_appends_under_existing_section():
    base = "Body.\n\n## Lessons (from agent memory)\n\n- first\n"
    out = append_lesson(base, "- second")
    assert out.count("## Lessons (from agent memory)") == 1
    assert "- first" in out and "- second" in out


def test_append_lesson_is_idempotent():
    base = "Body.\n\n## Lessons (from agent memory)\n\n- only\n"
    assert append_lesson(base, "- only") == base


def test_append_lesson_extends_section_followed_by_more_content():
    """The new bullet lands AFTER the existing bullets and ABOVE a following
    `##` section, preserving the heading's blank line and one contiguous list
    (the data-integrity case a section-as-last-thing test misses)."""
    base = ("Guide.\n\n"
            "## Lessons (from agent memory)\n\n"
            "- **Old** — first bullet.\n\n"
            "## Usage\n\nrun it.\n")
    out = append_lesson(base, "- **New** — second bullet.")
    assert out == ("Guide.\n\n"
                   "## Lessons (from agent memory)\n\n"
                   "- **Old** — first bullet.\n"
                   "- **New** — second bullet.\n\n"
                   "## Usage\n\nrun it.\n")
    # exactly one list, one heading, following section intact
    assert out.count("## Lessons (from agent memory)") == 1
    assert out.index("- **Old**") < out.index("- **New**") < out.index("## Usage")


# ── selection + apply ──────────────────────────────────────────────────

def test_preview_lists_promotable_without_writing(monkeypatch, tmp_path):
    pdir = _patterns_dir(monkeypatch, tmp_path)
    _seed_memory(recall_count=5)
    result = consolidate_skills(memory.get_store(), apply=False)
    [lesson] = result.lessons
    assert lesson.skill == _SLUG and lesson.skipped is None
    assert result.applied == 0
    assert "Lessons" not in (pdir / _SLUG / "SKILL.md").read_text()


def test_apply_writes_section_and_retires(monkeypatch, tmp_path):
    pdir = _patterns_dir(monkeypatch, tmp_path)
    mid = _seed_memory(recall_count=5)
    result = consolidate_skills(memory.get_store(), apply=True)
    assert result.applied == 1 and _SLUG in result.changed_skills
    body = (pdir / _SLUG / "SKILL.md").read_text()
    assert "## Lessons (from agent memory)" in body
    assert "Restart backend for E2E" in body
    assert memory.get_store().get_dict(mid)["status"] == "retired"


def test_below_bar_memory_is_not_selected(monkeypatch, tmp_path):
    _patterns_dir(monkeypatch, tmp_path)
    _seed_memory(recall_count=1)  # bar default 3
    result = consolidate_skills(memory.get_store(), apply=True)
    assert result.lessons == [] and result.applied == 0


def test_manual_pattern_is_never_auto_written(monkeypatch, tmp_path):
    pdir = _patterns_dir(monkeypatch, tmp_path, manual=True)
    mid = _seed_memory(recall_count=5)
    result = consolidate_skills(memory.get_store(), apply=True)
    [lesson] = result.lessons
    assert lesson.skipped and "manual" in lesson.skipped
    assert result.applied == 0
    assert "Lessons" not in (pdir / _SLUG / "SKILL.md").read_text()
    assert memory.get_store().get_dict(mid)["status"] == "active"


def test_sourceless_skill_is_skipped(monkeypatch, tmp_path):
    _patterns_dir(monkeypatch, tmp_path, has_source=False)
    _seed_memory(recall_count=5)
    result = consolidate_skills(memory.get_store(), apply=True)
    [lesson] = result.lessons
    assert lesson.skipped and "no pattern source" in lesson.skipped
    assert result.applied == 0
