"""Consolidate proven skill-memories into the skill's own SKILL.md.

The hard, write-time complement to the soft `<skill_experience>` recall
(hook_manager/handlers/memory_recall.py): once a memory filed under a
`skill-<slug>` meta-leaf has proven itself (its `recall_count` clears the
promotion bar), it can *graduate* into that skill's guide — appended to a
`## Lessons (from agent memory)` section in the pattern source SKILL.md —
and then be retired, so the lesson lives in the skill text instead of
riding recall on every invocation.

Ownership is respected: a `manual: true` pattern is user-authored, so it is
**never** auto-written — only proposed (the human applies it by hand). Only
regin-owned (non-manual) sources are edited on `apply=True`. A skill with no
pattern source under `settings.patterns_dir` is skipped (we can't edit a
guide we don't own the source of).

Pure of provider deploys: this writes the pattern *source* and retires the
memory; re-deploying the edited skill to the active agent is the CLI's job
(`regin memory consolidate-skills`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from lib.activity_log import get_activity_logger

log = get_activity_logger("memory")

_LESSON_HEADING = "## Lessons (from agent memory)"


@dataclass
class SkillLesson:
    """One promotable memory and where it would land."""

    memory_id: str
    skill: str                       # the skill slug
    title: str
    bullet: str                      # the markdown line appended to the guide
    skill_md: Optional[str] = None   # pattern source path, or None if absent
    applied: bool = False
    skipped: Optional[str] = None    # reason this was not written, or None


@dataclass
class ConsolidationResult:
    lessons: list[SkillLesson] = field(default_factory=list)
    applied: int = 0
    changed_skills: set[str] = field(default_factory=set)


def _skill_leaves() -> dict[str, dict]:
    """The `skill-<slug>` leaves of the global `skills` meta-root."""
    from lib.topics.meta_roots import load_global_meta_topics
    return {nid: n for nid, n in load_global_meta_topics().items()
            if n.get("parent_id") == "skills"}


def _source_md(slug: str) -> Optional[Path]:
    """The pattern source SKILL.md for `slug`, or None when regin owns no
    source for it (e.g. a global/provider-only skill)."""
    from lib.settings import settings
    path = Path(settings.patterns_dir) / slug / "SKILL.md"
    return path if path.is_file() else None


def _is_manual(md_path: Path) -> bool:
    """True when the pattern frontmatter marks it user-owned (`manual: true`)."""
    from lib.patterns.pattern_promoter import _parse_frontmatter
    try:
        fm, _ = _parse_frontmatter(md_path.read_text(encoding="utf-8"))
    except OSError:
        return False
    val = fm.get("manual")
    return str(val).strip().lower() in ("true", "1", "yes") if val is not None \
        else False


def _bullet(mem: dict) -> str:
    """The guide line for a memory: title + body, kept to one paragraph."""
    title = (mem.get("title") or "").strip()
    body = " ".join((mem.get("body") or "").split())
    lead = f"**{title}** — " if title else ""
    return f"- {lead}{body}"


def _lessons_insert_index(lines: list[str], heading: int) -> int:
    """Index at which to insert a new bullet: just after the last non-empty
    line of the Lessons section (which ends at the next H2 or EOF), so the new
    bullet joins the existing list rather than splitting it or jumping a
    following `##` section."""
    end = len(lines)
    for i in range(heading + 1, len(lines)):
        if lines[i].startswith("## "):
            end = i
            break
    insert_at = heading + 1
    for i in range(heading + 1, end):
        if lines[i].strip():
            insert_at = i + 1
    return insert_at


def append_lesson(body: str, bullet: str) -> str:
    """Return `body` with `bullet` added under the lessons heading — creating
    the section at the end when it's absent, else appending *after the last
    existing bullet* in the section (above any following `##` section, keeping
    the heading's blank line and the single list intact). Idempotent: a bullet
    already present is not duplicated."""
    if bullet in body:
        return body
    if _LESSON_HEADING not in body:
        sep = "" if body.endswith("\n\n") else ("\n" if body.endswith("\n")
                                                else "\n\n")
        return f"{body}{sep}{_LESSON_HEADING}\n\n{bullet}\n"
    lines = body.split("\n")
    heading = next(i for i, l in enumerate(lines)
                   if l.strip() == _LESSON_HEADING)
    lines.insert(_lessons_insert_index(lines, heading), bullet)
    return "\n".join(lines)


def _promotable(store, leaf_id: str, min_recall: int) -> list[dict]:
    """Active memories under a skill leaf whose recall_count clears the bar."""
    ids = store.memories_for_topic_subtree([leaf_id], scope=None)
    out = []
    for mid in ids:
        m = store.get_dict(mid)
        if m and (m.get("recall_count") or 0) >= min_recall:
            out.append(m)
    return out


def _lesson_for(mem: dict, slug: str) -> SkillLesson:
    """Resolve where one promotable memory lands and why it might be skipped
    (no source / user-owned) — without writing anything."""
    lesson = SkillLesson(memory_id=mem["id"], skill=slug,
                         title=mem.get("title") or mem["id"][:8],
                         bullet=_bullet(mem))
    src = _source_md(slug)
    if src is None:
        lesson.skipped = "no pattern source (skill not owned here)"
        return lesson
    lesson.skill_md = str(src)
    if _is_manual(src):
        lesson.skipped = "manual pattern (user-owned; apply by hand)"
    return lesson


def _apply_lesson(store, lesson: SkillLesson, result: ConsolidationResult,
                  retire: bool) -> None:
    """Write one lesson into its (non-manual) source and retire the memory."""
    src = Path(lesson.skill_md)  # type: ignore[arg-type]
    src.write_text(append_lesson(src.read_text(encoding="utf-8"),
                                 lesson.bullet), encoding="utf-8")
    lesson.applied = True
    result.applied += 1
    result.changed_skills.add(lesson.skill)
    if retire:
        store.update(lesson.memory_id, status="retired")
        store.record_validation(lesson.memory_id, validator="consolidate",
                                action="consolidated_into_skill",
                                note=f"folded into {lesson.skill} SKILL.md")
    log.write("skill_lesson_consolidated", memory_id=lesson.memory_id,
              skill=lesson.skill, retired=retire)


def consolidate_skills(store, *, apply: bool = False,
                       skill: Optional[str] = None,
                       min_recall: Optional[int] = None,
                       retire: bool = True) -> ConsolidationResult:
    """Find skill-memories over the promotion bar and (optionally) fold them
    into their skill's SKILL.md. Preview-only by default (`apply=False`).

    `apply=True` writes only **non-manual** sources and retires those memories;
    manual / source-less lessons are always preview-only (carried in the result
    with a `skipped` reason). `skill` limits to one slug; `min_recall` overrides
    the configured bar; `retire=False` writes the guide without retiring (tests).
    """
    from lib.settings import settings
    cfg = settings.agent_memory
    bar = cfg.consolidate_skill_min_recall if min_recall is None else min_recall
    result = ConsolidationResult()
    for leaf_id in _skill_leaves():
        slug = leaf_id[len("skill-"):]
        if skill is not None and slug != skill:
            continue
        for mem in _promotable(store, leaf_id, bar):
            lesson = _lesson_for(mem, slug)
            if apply and lesson.skipped is None:
                _apply_lesson(store, lesson, result, retire)
            result.lessons.append(lesson)
    return result


__all__ = ["consolidate_skills", "append_lesson", "SkillLesson",
           "ConsolidationResult"]
