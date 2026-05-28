"""Concealment experiments for pattern guides.

An "experiment" hides one or more H2 sections of a pattern's SKILL.md before
it is deployed as a Claude Code skill. The goal is to measure impact: with
the section concealed, do rules fire more often (see /rules/triggers)?

The conceal filter is a pure function — all state lives in the `experiments`
table. Exactly one experiment per pattern may be active at a time.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import select

from lib.settings import settings
from lib.orm import SessionLocal
from lib.orm.models import Experiment


# ---------------------------------------------------------------------------
# H2 parsing + conceal filter (pure)
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(r'^(#{2,3}) .+$', re.MULTILINE)
_ANY_HEADING_RE = re.compile(r'^(#{1,6}) ')


def list_sections(pattern_slug: str) -> list[str]:
    """Return `## Heading` and `### Heading` strings in the pattern's
    SKILL.md, in document order. Used to populate conceal-spec checkboxes.

    H2 and H3 are both conceal-able — H2 hides the whole top-level section
    (including any nested H3s); H3 hides only the sub-section down to the
    next H3 or H2.
    """
    path = os.path.join(str(settings.patterns_dir), pattern_slug, 'SKILL.md')
    if not os.path.isfile(path):
        return []
    with open(path, 'r') as f:
        content = f.read()
    # Strip frontmatter so headings inside it (there shouldn't be any) don't
    # leak into the list.
    parts = content.split('---', 2)
    body = parts[2] if len(parts) >= 3 else content
    return [m.group(0).rstrip() for m in _HEADING_RE.finditer(body)]


def apply_conceal(body: str, sections: list[str]) -> str:
    """Return `body` with every heading in `sections` (and its content)
    removed. Pure — no I/O.

    A concealed section spans from its heading line up to (but not
    including) the next heading of equal-or-higher level. So:

    - `## Foo` strips from `## Foo` until the next `## ` or `# ` heading —
      any `### Bar` nested inside `## Foo` is removed with it.
    - `### Bar` strips from `### Bar` until the next `### `, `## `, or
      `# ` heading — the surrounding `## Foo` header and its other
      sub-sections survive.

    Matching is exact after stripping trailing whitespace.
    """
    if not sections:
        return body
    target = {s.rstrip() for s in sections}

    lines = body.splitlines(keepends=True)
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        m = _ANY_HEADING_RE.match(line)
        if m and line.rstrip() in target:
            my_level = len(m.group(1))
            j = i + 1
            while j < n:
                nxt_match = _ANY_HEADING_RE.match(lines[j])
                if nxt_match and len(nxt_match.group(1)) <= my_level:
                    break
                j += 1
            i = j
            continue
        out.append(line)
        i += 1
    return ''.join(out)


# ---------------------------------------------------------------------------
# CRUD — via SQLModel
# ---------------------------------------------------------------------------

def _to_dict(row: Experiment) -> dict:
    """Project an Experiment row into the legacy dict shape.

    Shape: raw columns plus a derived `sections` list parsed from
    `conceal_spec`. Blueprints render against `sections`; preserve.
    """
    try:
        sections = json.loads(row.conceal_spec or "[]")
    except json.JSONDecodeError:
        sections = []
    return {
        "id": row.id,
        "pattern_slug": row.pattern_slug,
        "name": row.name,
        "conceal_spec": row.conceal_spec,
        "active": row.active,
        "created_at": row.created_at,
        "activated_at": row.activated_at,
        "sections": sections,
    }


def list_for_pattern(pattern_slug: str) -> list[dict]:
    with SessionLocal() as session:
        stmt = (
            select(Experiment)
            .where(Experiment.pattern_slug == pattern_slug)
            .order_by(Experiment.created_at.desc())
        )
        return [_to_dict(r) for r in session.exec(stmt).all()]


def patterns_with_active() -> set[str]:
    """Return set of pattern_slug values that have at least one active experiment."""
    with SessionLocal() as session:
        stmt = (
            select(Experiment.pattern_slug)
            .where(Experiment.active == 1)
            .distinct()
        )
        return set(session.exec(stmt).all())


def list_all() -> list[dict]:
    with SessionLocal() as session:
        stmt = select(Experiment).order_by(
            Experiment.pattern_slug, Experiment.created_at.desc()
        )
        return [_to_dict(r) for r in session.exec(stmt).all()]


def get(experiment_id: int) -> Optional[dict]:
    with SessionLocal() as session:
        row = session.get(Experiment, experiment_id)
        return _to_dict(row) if row else None


def create(pattern_slug: str, name: str, sections: list[str]) -> int:
    with SessionLocal() as session:
        row = Experiment(
            pattern_slug=pattern_slug, name=name,
            conceal_spec=json.dumps(sections),
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return row.id or 0


def update(experiment_id: int, name: str, sections: list[str]) -> Optional[str]:
    """Update an experiment's name and/or conceal spec. Returns the
    pattern_slug (so the caller can redeploy if the experiment was active),
    or None if the id does not exist."""
    with SessionLocal() as session:
        row = session.get(Experiment, experiment_id)
        if row is None:
            return None
        row.name = name
        row.conceal_spec = json.dumps(sections)
        session.add(row)
        session.commit()
        return row.pattern_slug


def delete(experiment_id: int) -> None:
    with SessionLocal() as session:
        row = session.get(Experiment, experiment_id)
        if row is None:
            return
        session.delete(row)
        session.commit()


def activate(experiment_id: int) -> Optional[str]:
    """Mark an experiment active, deactivating any other experiment on the
    same pattern. Returns the pattern_slug (for redeploy) or None if the
    experiment does not exist."""
    with SessionLocal() as session:
        row = session.get(Experiment, experiment_id)
        if row is None:
            return None
        slug = row.pattern_slug

        # Deactivate every other experiment on this pattern.
        other_stmt = select(Experiment).where(
            Experiment.pattern_slug == slug,
            Experiment.id != experiment_id,
        )
        for other in session.exec(other_stmt).all():
            other.active = 0
            other.activated_at = None
            session.add(other)

        row.active = 1
        row.activated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        session.add(row)
        session.commit()
        return slug


def deactivate(experiment_id: int) -> Optional[str]:
    with SessionLocal() as session:
        row = session.get(Experiment, experiment_id)
        if row is None:
            return None
        row.active = 0
        row.activated_at = None
        session.add(row)
        session.commit()
        return row.pattern_slug


def get_active(pattern_slug: str) -> Optional[tuple[int, list[str]]]:
    """Return (experiment_id, sections) for the active experiment on
    `pattern_slug`, or None."""
    with SessionLocal() as session:
        stmt = (
            select(Experiment)
            .where(Experiment.pattern_slug == pattern_slug,
                   Experiment.active == 1)
            .limit(1)
        )
        row = session.exec(stmt).first()
        if row is None:
            return None
        try:
            sections = json.loads(row.conceal_spec or "[]")
        except json.JSONDecodeError:
            sections = []
        return row.id, sections


def get_active_id(pattern_slug: Optional[str]) -> Optional[int]:
    if not pattern_slug:
        return None
    active = get_active(pattern_slug)
    return active[0] if active else None
