"""Creating the task that will close a defect, for whoever asked.

The verb lives here rather than in `cli/commands/defects.py` because the
browser now offers it too, and a second copy would let the two surfaces write
different task bodies for the same defect — the board and the ledger
describing one bug in two wordings is exactly what storing the symptom
verbatim exists to prevent. The wording itself is the `defect-fix-task`
prompt surface, editable on the prompts page like every other agent prompt.

Pointing at an *existing* task stays in `store.set_fix_item`: that is a
one-column write with no second row to author.
"""

from __future__ import annotations

import json

from lib.activity_log import get_activity_logger
from lib.defects import store

log = get_activity_logger("defects")


def create_for(defect_id: int):
    """Capture the task, attach it, and never touch it again.

    Title is the symptom verbatim; the body is the `defect-fix-task` prompt
    surface. No label, assignee or priority — those are fields with human
    writers. `external_id` makes it one task per defect: a second click, a
    second tab or the CLI after the browser all resolve to the first task
    instead of filing a duplicate.
    """
    from lib.tasks import store as task_store  # noqa: PLC0415

    row = store.get(defect_id)
    if row.feature_id is None:
        raise store.DefectError(
            f"#{row.id} is unattributed, so there is no feature to file "
            f"under — `regin defects attribute {row.id} <ID>` first")
    if row.fix_task_id is not None:
        if _is_own_task(row):
            return row
        raise store.DefectError(
            f"#{row.id} already points at task #{row.fix_task_id}, which was "
            f"not created from it — re-point with `regin defects fix-item "
            f"{row.id} --task <ID>` instead of creating a second one")
    # A replay here means the defect's own task was unlinked by `--clear`:
    # it is relinked rather than filed again, one task per defect for good.
    view, created = task_store.capture_task(
        title=row.symptom[:200], body=task_body(row), source="defect",
        external_id=f"defect:{row.id}", feature_id=row.feature_id)
    pointed = store.set_fix_item(row.id, view["id"])
    if created:
        _copy_images(row.id, view["id"])
    return pointed


def _is_own_task(row) -> bool:
    from lib.orm import SessionLocal  # noqa: PLC0415
    from lib.orm.models.tasks import AgentTask  # noqa: PLC0415

    with SessionLocal() as session:
        task = session.get(AgentTask, row.fix_task_id)
    return (task is not None and task.source == "defect"
            and task.external_id == f"defect:{row.id}")


def _copy_images(defect_id: int, task_id: int) -> None:
    """A defect's screenshots reach no model; a task's ride its runs. Each
    keeps its `idx`, so an `![](image:N)` in the symptom still names it."""
    from lib.defects import images as defect_images  # noqa: PLC0415
    from lib.tasks import images as task_images  # noqa: PLC0415

    for image in defect_images.list_for(defect_id):
        found = defect_images.get_bytes(defect_id, image["idx"])
        if found is None:
            continue
        try:
            task_images.attach(task_id, found[0], want_idx=image["idx"])
        except ValueError:
            log.error("defect_image_not_copied", defect_id=defect_id,
                      task_id=task_id, idx=image["idx"], exc_info=True)


def task_body(row) -> str:
    from lib.features import store as feature_store  # noqa: PLC0415
    from lib.prompts import render_surface  # noqa: PLC0415
    from lib.prompts.surfaces.defects import FIX_TASK_SURFACE_ID  # noqa: PLC0415

    feature = feature_store.get(row.feature_id)
    paths = ", ".join(json.loads(row.suspect_paths_json or "[]"))
    return render_surface(FIX_TASK_SURFACE_ID, {
        "defect_id": row.id,
        "symptom": row.symptom,
        "feature_named": f"#{row.feature_id}"
                         + (f" ({feature.title})" if feature else ""),
        "round": row.round,
        "details": _details(row, paths),
        "reported_session": row.reported_by_trace or "",
        "suspect_paths": paths,
        "missing_check": row.missing_check or "",
    }).rstrip()


def _details(row, paths: str) -> str:
    """Only the lines that apply. The named check rides along because it is
    the one line of a defect that describes *future* work: the task is where
    that test gets written."""
    lines = []
    if row.reported_by_trace:
        lines.append(f"reported in session {row.reported_by_trace}")
    if paths:
        lines.append("suspect paths: " + paths)
    if row.missing_check:
        lines.append(f"missing check: {row.missing_check}")
    return "\n".join(lines)
