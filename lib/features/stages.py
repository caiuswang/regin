"""A project's step stages: which steps a feature can open, and their prompts.

Not the feature's *lifecycle* stage (`lib/features/events.py::STAGES`, derived
from the ledger). A step stage is an entry in an ordered, per-project list —
Spec and Build by default — and its `role` is all the ledger ever sees: a step
task's `kind` is the role, its `stage_key` the key.

Resolution: a project with rows of its own reads them; one without reads the
global default rows (`project_id IS NULL`), as does a feature with no project.
The first write to a project copies the defaults — and their prompts — into
the project's own rows, then applies the change, so editing one project never
reaches another.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Callable, Optional

from sqlalchemy.exc import IntegrityError
from sqlmodel import select, text

from lib.activity_log import get_activity_logger
from lib.orm import SessionLocal
from lib.orm.models.prompts import PromptTemplate
from lib.orm.models.stages import ProjectStage
from lib.orm.models.sync import Repo
from lib.orm.models.tasks import TASK_KIND_BUILD, TASK_KIND_SPEC

log = get_activity_logger("features")

ROLES: tuple[str, ...] = (TASK_KIND_SPEC, TASK_KIND_BUILD)
PROMPT_KIND = "stage"
LABEL_MAX = 80
PROMPT_MAX = 20000
_KEY_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
_PROMPT_VARIABLES = (
    '[{"name": "feature_named", "description": "The feature as the prompt '
    'names it.", "example": "#80 (Agent SDK runs)", "required": false}, '
    '{"name": "spec_cmd", "description": "The literal `regin feature spec` '
    'write command for this feature.", "example": "", "required": false}, '
    '{"name": "stage_label", "description": "The stage this step belongs to.",'
    ' "example": "Spec", "required": false}]')


class StageError(ValueError):
    """A refused stage write, carrying the HTTP status and the field it names."""

    status = 400

    def __init__(self, message: str, field: Optional[str] = None) -> None:
        super().__init__(message)
        self.field = field


class StageNotFound(StageError):
    status = 404


class StageConflict(StageError):
    status = 409


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def require_project(project_id: Optional[int]) -> None:
    """Raise `StageNotFound` unless `project_id` is None or a registered repo."""
    if project_id is None:
        return
    with SessionLocal() as session:
        if session.get(Repo, project_id) is None:
            raise StageNotFound(f"unknown project: {project_id}")


def _own_rows(session, project_id: Optional[int]) -> list[ProjectStage]:
    clause = (ProjectStage.project_id.is_(None) if project_id is None
              else ProjectStage.project_id == project_id)
    return list(session.exec(select(ProjectStage).where(clause).order_by(
        ProjectStage.position, ProjectStage.id)).all())


def _rows(session, project_id: Optional[int]) -> tuple[list[ProjectStage], bool]:
    """(the rows the project reads, whether they are the inherited defaults)."""
    if project_id is not None:
        own = _own_rows(session, project_id)
        if own:
            return own, False
    return _own_rows(session, None), project_id is not None


def _prompt_bodies(session, slugs: set[str]) -> dict[str, str]:
    if not slugs:
        return {}
    rows = session.exec(select(PromptTemplate).where(
        PromptTemplate.slug.in_(slugs))).all()
    return {row.slug: row.body for row in rows}


def _view(row: ProjectStage, bodies: dict[str, str]) -> dict:
    return {
        "id": row.id, "project_id": row.project_id, "key": row.key,
        "label": row.label, "role": row.role, "position": row.position,
        "prompt_slug": row.prompt_slug,
        "prompt_body": bodies.get(row.prompt_slug) if row.prompt_slug else None,
        "builtin": bool(row.builtin), "archived_at": row.archived_at,
    }


def _views(session, rows: list[ProjectStage]) -> list[dict]:
    bodies = _prompt_bodies(session, {r.prompt_slug for r in rows if r.prompt_slug})
    return [_view(row, bodies) for row in rows]


def list_stages(project_id: Optional[int], *,
                include_archived: bool = True) -> dict:
    """`{project_id, inherited, stages}` — every stage the project reads, in
    position order. `inherited` is True while the project has no rows of its
    own and reads the global defaults."""
    with SessionLocal() as session:
        rows, inherited = _rows(session, project_id)
        if not include_archived:
            rows = [r for r in rows if r.archived_at is None]
        return {"project_id": project_id, "inherited": inherited,
                "stages": _views(session, rows)}


def resolve(project_id: Optional[int], key: Optional[str], *,
            include_archived: bool = False) -> Optional[dict]:
    """The stage `key` names in the project's list, or None."""
    if not key:
        return None
    with SessionLocal() as session:
        rows, _ = _rows(session, project_id)
        for row in rows:
            if row.key == key and (include_archived or row.archived_at is None):
                return _view(row, _prompt_bodies(
                    session, {row.prompt_slug} if row.prompt_slug else set()))
    return None


def _prompt_slug(project_id: Optional[int], key: str) -> str:
    return f"stage-{'global' if project_id is None else project_id}-{key}"


def _write_prompt(session, project_id: Optional[int], stage: ProjectStage,
                  body: str, now: str) -> None:
    """Point the stage at the project's own prompt row, creating it."""
    slug = _prompt_slug(project_id, stage.key)
    row = session.exec(select(PromptTemplate).where(
        PromptTemplate.slug == slug)).first()
    if row is None:
        row = PromptTemplate(
            slug=slug, label=f"Stage — {stage.label}", body=body,
            description="The prompt a run of this step stage is told.",
            kind=PROMPT_KIND, variables=_PROMPT_VARIABLES, applies_to="[]",
            default_for_providers="[]", tags="[]", builtin=stage.builtin,
            created_at=now, updated_at=now)
    else:
        row.body = body
        row.updated_at = now
    session.add(row)
    stage.prompt_slug = slug


def _copy_defaults(session, project_id: int, now: str) -> None:
    """Copy-on-write: give the project its own copy of every default stage and
    its prompt, inside the caller's transaction."""
    defaults = _own_rows(session, None)
    bodies = _prompt_bodies(session, {r.prompt_slug for r in defaults
                                      if r.prompt_slug})
    for src in defaults:
        row = ProjectStage(
            project_id=project_id, key=src.key, label=src.label,
            role=src.role, position=src.position, builtin=src.builtin,
            archived_at=src.archived_at, created_at=now, updated_at=now)
        if src.prompt_slug and src.prompt_slug in bodies:
            _write_prompt(session, project_id, row,
                          bodies[src.prompt_slug], now)
        session.add(row)
    session.flush()


def _writing(project_id: Optional[int],
             change: Callable[..., dict]) -> dict:
    """Run `change(session, rows, now)` against the project's own rows,
    copying the defaults first when it has none.

    The copy and the change commit together, so a refused change leaves the
    project reading the defaults. A concurrent first write that copied first
    makes this copy collide on the unique key; the retry then finds the
    project's rows and copies nothing.
    """
    require_project(project_id)
    try:
        return _attempt(project_id, change)
    except IntegrityError:
        return _attempt(project_id, change)


def _attempt(project_id: Optional[int], change: Callable[..., dict]) -> dict:
    now = _now()
    with SessionLocal() as session:
        rows = _own_rows(session, project_id)
        copied = not rows and project_id is not None
        if copied:
            _copy_defaults(session, project_id, now)
            rows = _own_rows(session, project_id)
        result = change(session, rows, now)
        session.commit()
    if copied:
        log.write("project_stages_copied", project_id=project_id)
    return result


def _find(rows: list[ProjectStage], key: str) -> ProjectStage:
    for row in rows:
        if row.key == key:
            return row
    raise StageNotFound(f"unknown stage: {key}", "key")


def _label(value) -> str:
    label = value.strip() if isinstance(value, str) else ""
    if not label or len(label) > LABEL_MAX:
        raise StageError(f"label must be 1–{LABEL_MAX} characters", "label")
    return label


def _role(value) -> str:
    if value not in ROLES:
        raise StageError(f"role must be one of {', '.join(ROLES)}", "role")
    return value


def _prompt(value) -> str:
    if not isinstance(value, str) or len(value) > PROMPT_MAX:
        raise StageError(f"prompt must be text of at most {PROMPT_MAX} "
                         "characters", "prompt")
    return value


def add_stage(project_id: Optional[int], payload: dict) -> dict:
    """Append a custom stage to the project's list."""
    key = payload.get("key")
    if not isinstance(key, str) or not _KEY_RE.match(key):
        raise StageError("key must match ^[a-z][a-z0-9-]{0,31}$", "key")
    label = _label(payload.get("label"))
    role = _role(payload.get("role"))
    prompt = _prompt(payload["prompt"]) if payload.get("prompt") is not None else None

    def change(session, rows, now):
        if any(row.key == key for row in rows):
            raise StageConflict(f"stage already exists: {key}", "key")
        row = ProjectStage(
            project_id=project_id, key=key, label=label, role=role,
            position=max((r.position for r in rows), default=-1) + 1,
            builtin=0, created_at=now, updated_at=now)
        if prompt is not None:
            _write_prompt(session, project_id, row, prompt, now)
        session.add(row)
        session.flush()
        return _views(session, [row])[0]

    view = _writing(project_id, change)
    log.write("project_stage_added", project_id=project_id, key=key, role=role)
    return view


def update_stage(project_id: Optional[int], key: str, payload: dict) -> dict:
    """Rename a stage, change a custom stage's role, or write its prompt."""
    label = _label(payload["label"]) if "label" in payload else None
    role = _role(payload["role"]) if "role" in payload else None
    prompt = _prompt(payload["prompt"]) if "prompt" in payload else None

    def change(session, rows, now):
        row = _find(rows, key)
        if role is not None and role != row.role:
            if row.builtin:
                raise StageError("a built-in stage's role is fixed", "role")
            row.role = role
        if label is not None:
            row.label = label
        if prompt is not None:
            _write_prompt(session, project_id, row, prompt, now)
        row.updated_at = now
        session.add(row)
        session.flush()
        return _views(session, [row])[0]

    view = _writing(project_id, change)
    log.write("project_stage_updated", project_id=project_id, key=key,
              fields=sorted(payload))
    return view


def _has_tasks(session, project_id: Optional[int], key: str) -> bool:
    """Whether any step task was opened from this stage in this list.

    A project's list serves the features filed under it; the default list
    serves features with no project and features of every project that has
    not yet copied it.
    """
    if project_id is not None:
        scope = "f.project_id = :p"
    else:
        scope = ("(f.project_id IS NULL OR f.project_id NOT IN "
                 "(SELECT project_id FROM project_stages "
                 "WHERE project_id IS NOT NULL))")
    return session.exec(text(
        "SELECT 1 FROM agent_tasks t JOIN features f ON f.id = t.feature_id "
        f"WHERE t.stage_key = :k AND {scope} LIMIT 1"
    ).bindparams(k=key, **({"p": project_id} if project_id is not None
                           else {}))).first() is not None


def remove_stage(project_id: Optional[int], key: str) -> dict:
    """Delete a custom stage, or archive it when tasks were opened from it.

    A built-in stage can be neither: the ledger needs a build entry, and the
    spec/build keys are what existing step tasks are matched by.
    """
    def change(session, rows, now):
        row = _find(rows, key)
        if row.builtin:
            raise StageConflict("a built-in stage cannot be deleted or "
                                "archived", "key")
        if _has_tasks(session, project_id, key):
            row.archived_at = row.archived_at or now
            row.updated_at = now
            session.add(row)
            session.flush()
            return {"archived": True, "deleted": False,
                    "stage": _views(session, [row])[0]}
        if row.prompt_slug == _prompt_slug(project_id, key):
            prompt = session.exec(select(PromptTemplate).where(
                PromptTemplate.slug == row.prompt_slug)).first()
            if prompt is not None:
                session.delete(prompt)
        session.delete(row)
        return {"archived": False, "deleted": True, "stage": None}

    result = _writing(project_id, change)
    log.write("project_stage_removed", project_id=project_id, key=key,
              archived=result["archived"])
    return result


def reorder(project_id: Optional[int], keys) -> dict:
    """Set the order of the project's live stages; archived ones follow."""
    if (not isinstance(keys, list)
            or not all(isinstance(k, str) for k in keys)
            or len(set(keys)) != len(keys)):
        raise StageError("keys must be a list of distinct stage keys", "keys")

    def change(session, rows, now):
        live = [r for r in rows if r.archived_at is None]
        if set(keys) != {r.key for r in live}:
            raise StageError("keys must name every live stage exactly once",
                             "keys")
        by_key = {r.key: r for r in live}
        ordered = [by_key[k] for k in keys] + [r for r in rows
                                               if r.archived_at is not None]
        for position, row in enumerate(ordered):
            row.position = position
            row.updated_at = now
            session.add(row)
        return {}

    _writing(project_id, change)
    log.write("project_stages_reordered", project_id=project_id, keys=keys)
    return list_stages(project_id)


__all__ = [
    "ROLES", "StageConflict", "StageError", "StageNotFound", "add_stage",
    "list_stages", "remove_stage", "reorder", "require_project",
    "resolve", "update_stage",
]
