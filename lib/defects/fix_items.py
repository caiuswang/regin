"""Creating the task that will close a defect, for whoever asked.

The verb lives here rather than in `cli/commands/defects.py` because the
browser now offers it too, and a second copy would let the two surfaces write
different task bodies for the same defect — the board and the ledger
describing one bug in two wordings is exactly what storing the symptom
verbatim exists to prevent. The wording itself is the `defect-fix-task`
prompt surface, editable on the prompts page like every other agent prompt.

Pointing at an *existing* task stays in `store.set_fix_item`: that is a
one-column write with no second row to author. The body cites every defect
the task fixes and is re-rendered as defects join or leave it, while it is
still the one regin wrote (`store.refresh_fix_tasks`); a hand-written body is
left alone, and its runs still read every linked defect live
(`run_prompt._cited_refs`).
"""

from __future__ import annotations

import json

from sqlmodel import select

from lib.activity_log import get_activity_logger
from lib.defects import store
from lib.orm.models.tasks import TASK_KIND_FIX
from lib.refs.grammar import format_ref

log = get_activity_logger("defects")


class BatchRefused(store.DefectError):
    """A batch with at least one defect that cannot join the task. Nothing was
    written; `blocking` names every offender so a surface can mark them all
    at once instead of one refusal per retry."""

    def __init__(self, message: str, blocking: list[int]):
        super().__init__(message)
        self.blocking = blocking


def create_for(defect_id: int):
    """Capture the task, attach it, and never touch it again.

    Title is the symptom verbatim, a shelf label regin recomputes only while
    nobody has renamed it (`store.refresh_fix_tasks`); the body is the `defect-fix-task`
    prompt surface. No label, assignee or priority — those are fields with
    human writers. `external_id` makes it one task per defect: a second click,
    a second tab or the CLI after the browser all resolve to the first task
    instead of filing a duplicate.

    A defect still open over a task that reads finished (fix drift) gets a
    NEW task and is re-pointed at it: the finished one's key is spent, so the
    new one is keyed on the task it replaces.
    """
    row = store.get(defect_id)
    if row.feature_id is None:
        raise store.DefectError(
            f"#{row.id} is unattributed, so there is no feature to file "
            f"under — `regin defects attribute {row.id} <ID>` first",
            remedy=store.remedy("attribute", row.id))
    if row.fix_task_id is not None:
        if store.is_fix_drift(row, _status(row.fix_task_id)):
            _create([row], replacing=row.fix_task_id)
            return store.get(row.id)
        if _is_own_task(row):
            return row
        raise store.DefectError(
            f"#{row.id} already points at task #{row.fix_task_id}, which was "
            f"not created from it — re-point with `regin defects fix-item "
            f"{row.id} --task <ID>` instead of creating a second one")
    _create([row])
    return store.get(row.id)


def create_batch(defect_ids) -> tuple[int, list]:
    """One fix task for several defects of one feature, or nothing at all.

    Keyed on the lowest id, so the same selection posted twice — or the
    lowest one alone through `create_for` — resolves to the same task. Every
    defect must already point at that task or at none: a defect re-pointed
    elsewhere is refused rather than silently pulled back.
    """
    ids = sorted(set(defect_ids))
    if not ids:
        raise store.DefectError("name at least one defect")
    task_id = _create([store.get(i) for i in ids])
    return task_id, [store.get(i) for i in ids]


def _create(rows, replacing=None) -> int:
    from lib.tasks import store as task_store  # noqa: PLC0415

    lead = rows[0]
    external_id = f"defect:{lead.id}"
    if replacing is not None:
        external_id += f":after:{replacing}"
    blocking = _blocking(rows, _task_keyed(external_id), replacing)
    if blocking:
        raise BatchRefused(
            "; ".join(f"#{i} {why}" for i, why in blocking.items())
            + " — nothing was created", list(blocking))
    title = store.fix_task_title(rows)
    # A replay here means the lowest defect's task was unlinked by `--clear`:
    # it is relinked rather than filed again, one task per defect for good.
    view, _ = task_store.capture_task(
        title=title, auto_title=title, source="defect",
        external_id=external_id, feature_id=lead.feature_id,
        kind=TASK_KIND_FIX)
    # Written on a replay too while still empty: a first attempt that died
    # between the insert and this line would otherwise never get a body.
    if view["body"] is None:
        _seed_body(view["id"], task_body(rows))
    store.set_fix_items([row.id for row in rows], view["id"])
    return view["id"]


def _seed_body(task_id: int, body: str) -> None:
    from lib.orm import SessionLocal  # noqa: PLC0415
    from lib.orm.models.tasks import AgentTask  # noqa: PLC0415

    with SessionLocal() as session:
        task = session.get(AgentTask, task_id)
        task.body = task.auto_body = body
        session.add(task)
        session.commit()


def _task_keyed(external_id: str):
    from lib.orm import SessionLocal  # noqa: PLC0415
    from lib.orm.models.tasks import AgentTask  # noqa: PLC0415

    with SessionLocal() as session:
        task = session.exec(select(AgentTask).where(
            AgentTask.source == "defect",
            AgentTask.external_id == external_id)).first()
    return task.id if task is not None else None


def _blocking(rows, task_id, replacing=None) -> dict[int, str]:
    """`{defect_id: why}` for every row that cannot join `task_id`. The
    feature is the lowest attributed defect's, so one stray row is named
    rather than the whole selection."""
    feature_id = next(
        (r.feature_id for r in rows if r.feature_id is not None), None)
    why = {}
    for row in rows:
        if row.feature_id is None:
            why[row.id] = "is unattributed"
        elif row.feature_id != feature_id:
            why[row.id] = (f"belongs to feature #{row.feature_id}, "
                           f"not #{feature_id}")
        elif row.fix_task_id not in (None, task_id, replacing):
            why[row.id] = f"already points at task #{row.fix_task_id}"
    return why


def _status(task_id: int):
    from lib.orm import SessionLocal  # noqa: PLC0415
    from lib.orm.models.tasks import AgentTask  # noqa: PLC0415

    with SessionLocal() as session:
        task = session.get(AgentTask, task_id)
    return task.status if task is not None else None


def _is_own_task(row) -> bool:
    """Any defect-sourced task the row points at, not only the one keyed on
    its own id: a task several defects share was keyed on the lowest."""
    from lib.orm import SessionLocal  # noqa: PLC0415
    from lib.orm.models.tasks import AgentTask  # noqa: PLC0415

    with SessionLocal() as session:
        task = session.get(AgentTask, row.fix_task_id)
    return task is not None and task.source == "defect"


def task_body(rows) -> str:
    """Every defect and their feature are cited as references, never copied:
    a symptom edited or a feature renamed after the task was cut must not
    leave the task describing the old one. The run reads them live in its
    `<references>` block (`lib/tasks/run_prompt.py`), screenshots included —
    which is why none are copied onto the task."""
    from lib.features import store as feature_store  # noqa: PLC0415
    from lib.prompts import render_surface  # noqa: PLC0415
    from lib.prompts.surfaces.defects import FIX_TASK_SURFACE_ID  # noqa: PLC0415

    rows = sorted(rows, key=lambda r: r.id)
    row = rows[0]
    feature = feature_store.get(row.feature_id)
    title = feature.title if feature else ""
    paths = ", ".join(suspect_paths(row))
    return render_surface(FIX_TASK_SURFACE_ID, {
        "defects_named": "defect" if len(rows) == 1 else "defects",
        "defect_refs": "\n".join(
            "- " + format_ref("defect", str(r.id), r.symptom) for r in rows),
        "defect_id": row.id,
        "defect_ref": format_ref("defect", str(row.id), row.symptom),
        "feature_ref": format_ref("feature", str(row.feature_id), title),
        "symptom": row.symptom,
        "feature_named": f"#{row.feature_id}"
                         + (f" ({title})" if title else ""),
        "round": row.round,
        "details": "\n".join(facts(row)),
        "reported_session": row.reported_by_trace or "",
        "suspect_paths": paths,
        "missing_check": row.missing_check or "",
    }).rstrip()


def suspect_paths(row) -> list[str]:
    return json.loads(row.suspect_paths_json or "[]")


def facts(row) -> list[str]:
    """Only the facts that apply. The named check rides along because it is
    the one line of a defect that describes *future* work: the task is where
    that test gets written."""
    lines = []
    if row.reported_by_trace:
        lines.append(f"reported in session {row.reported_by_trace}")
    paths = ", ".join(suspect_paths(row))
    if paths:
        lines.append("suspect paths: " + paths)
    if row.missing_check:
        lines.append(f"missing check: {row.missing_check}")
    return lines
