"""Creating the task that will close a defect, for whoever asked.

The verb lives here rather than in `cli/commands/defects.py` because the
browser now offers it too, and a second copy would let the two surfaces write
different task bodies for the same defect — the board and the ledger
describing one bug in two wordings is exactly what storing the symptom
verbatim exists to prevent.

Pointing at an *existing* task stays in `store.set_fix_item`: that is a
one-column write with no second row to author.
"""

from __future__ import annotations

import json

from lib.defects import store


def create_for(defect_id: int):
    """Capture the task, attach it, and never touch it again.

    Title is the symptom verbatim. No label, assignee or priority — those are
    fields with human writers.
    """
    from lib.tasks import store as task_store  # noqa: PLC0415

    row = store.get(defect_id)
    if row.feature_id is None:
        raise store.DefectError(
            f"#{row.id} is unattributed, so there is no feature to file "
            f"under — `regin defects attribute {row.id} <ID>` first")
    view, _created = task_store.capture_task(
        title=row.symptom[:200], body=back_pointer(row), source="defect",
        feature_id=row.feature_id)
    return store.set_fix_item(row.id, view["id"])


def back_pointer(row) -> str:
    """What regin writes into the new task's body, and all it writes.

    The named check rides along because it is the one line of a defect that
    describes *future* work: the task is where that test gets written, and an
    agent reading the task otherwise has to go back to the ledger to learn
    what it was asked to prove.
    """
    paths = json.loads(row.suspect_paths_json or "[]")
    lines = [f"regin defect #{row.id}"]
    if row.reported_by_trace:
        lines.append(f"reported in session {row.reported_by_trace}")
    if paths:
        lines.append("suspect paths: " + ", ".join(paths))
    if row.missing_check:
        lines.append(f"missing check: {row.missing_check}")
    return "\n".join(lines)
