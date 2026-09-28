"""What a feature's operator should do next, and what shipping would leave open.

Both are readings over rows that already exist — the stage, the spec, the
feature's tasks and its defects — so neither is stored. The decision table is
R2 of feature #119: first match wins within a stage.

The index asks this for a whole page, so the task facts come back for every
feature on the page from one statement (`task_facts`); a per-row read is what
`lib/features/index.py` exists to avoid.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from sqlmodel import select

from lib.defects import escapes as escapes_module
from lib.features import events as events_module
from lib.features import store as feature_store
from lib.features import sync as sync_module
from lib.orm import SessionLocal
from lib.orm.models.defects import STATUS_OPEN, Defect
from lib.orm.models.features import FeatureEvent
from lib.orm.models.tasks import (
    AgentTask, TASK_STATUS_ARCHIVED, TASK_STATUS_ARMED, TASK_STATUS_DONE,
    TASK_STATUS_IDEA, TASK_STATUS_QUEUED, TASK_STATUS_RUNNING,
)
from lib.tasks import slicing
from lib.tasks.integrations import registry
from lib.tasks.next_step import has_unmet_dependency
from lib.tasks.transcribe import TRANSCRIBE_SOURCE

WRITE_SPEC = "write_spec"
SLICE = "slice"
ARM_SLICES = "arm_slices"
RUN = "run"
WATCH = "watch"
RESOLVE_DEFECTS = "resolve_defects"
SHIP = "ship"
REOPEN = "reopen"
NONE = "none"

_RUNNABLE = (TASK_STATUS_ARMED, TASK_STATUS_QUEUED)
_CLOSED_TASK = (TASK_STATUS_DONE, TASK_STATUS_ARCHIVED)

#: A tracker's last word, as the event kind regin folded it into.
_TRACKER_WORD = {
    events_module.SPECCED: "not started",
    events_module.BUILD_STARTED: "in progress",
    events_module.RESTARTED: "in progress",
    events_module.ABANDONED: "canceled",
}


@dataclass(frozen=True)
class TaskFacts:
    """A feature's unarchived tasks, reduced to what R2 asks of them."""

    slices: int = 0
    idea_slices: int = 0
    running_id: Optional[int] = None
    runnable_id: Optional[int] = None


@dataclass(frozen=True)
class NextStep:
    kind: str
    label: str
    target: Optional[str]

    def to_dict(self) -> dict:
        return {"kind": self.kind, "label": self.label, "target": self.target}


def _is_slice(task_source: Optional[str], external_id: Optional[str],
              feature_id: int) -> bool:
    return (task_source == TRANSCRIBE_SOURCE
            and (external_id or "").startswith(f"{feature_id}:"))


def task_facts(feature_ids: Iterable[int]) -> dict[int, TaskFacts]:
    """`TaskFacts` for every id, from one statement."""
    ids = list(feature_ids)
    if not ids:
        return {}
    with SessionLocal() as session:
        records = session.exec(
            select(AgentTask.feature_id, AgentTask.id, AgentTask.status,
                   AgentTask.source, AgentTask.external_id,
                   has_unmet_dependency())
            .where(AgentTask.feature_id.in_(ids),
                   AgentTask.status != TASK_STATUS_ARCHIVED)
            .order_by(AgentTask.id)).all()
    acc: dict[int, dict] = {fid: {"slices": 0, "idea_slices": 0,
                                  "running_id": None, "runnable_id": None}
                            for fid in ids}
    for record in records:
        _fold(acc[record[0]], *record)
    return {fid: TaskFacts(**facts) for fid, facts in acc.items()}


def _fold(facts: dict, feature_id: int, task_id: int, status: str,
          source: Optional[str], external_id: Optional[str],
          blocked: bool) -> None:
    if _is_slice(source, external_id, feature_id):
        facts["slices"] += 1
        facts["idea_slices"] += status == TASK_STATUS_IDEA
    if status == TASK_STATUS_RUNNING:
        facts["running_id"] = facts["running_id"] or task_id
    if status in _RUNNABLE and not blocked:
        facts["runnable_id"] = facts["runnable_id"] or task_id


def _plural(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _spec_step(feature_id: int, body: Optional[str],
               facts: TaskFacts) -> NextStep:
    page = f"/features/{feature_id}"
    if not (body or "").strip():
        return NextStep(WRITE_SPEC, "Write the spec", f"{page}#feature-spec")
    if not facts.slices:
        rows, refusal = slicing.preview(body or "")
        if refusal is not None:
            reason = refusal.missing[0] if refusal.missing else str(refusal)
            return NextStep(SLICE, f"Slice the spec — {reason}",
                            f"{page}#feature-spec")
        return NextStep(SLICE, f"Transcribe {_plural(len(rows), 'slice')}",
                        f"{page}#feature-spec")
    if facts.idea_slices:
        return NextStep(ARM_SLICES,
                        f"Arm {_plural(facts.idea_slices, 'slice')}",
                        f"{page}#feature-tasks")
    return _run_step(feature_id, facts)


def _run_step(feature_id: int, facts: TaskFacts) -> NextStep:
    target = (f"/tasks/{facts.runnable_id}" if facts.runnable_id
              else f"/features/{feature_id}#feature-tasks")
    return NextStep(RUN, "Run the next task", target)


def _building_step(feature_id: int, facts: TaskFacts,
                   open_defects: int) -> NextStep:
    if facts.running_id:
        return NextStep(WATCH, "A task is running — watch it",
                        f"/tasks/{facts.running_id}")
    if facts.runnable_id:
        return _run_step(feature_id, facts)
    if open_defects:
        return NextStep(
            RESOLVE_DEFECTS,
            f"{_plural(open_defects, 'open defect')} block a clean ship",
            f"/features/{feature_id}#feature-defects")
    return NextStep(SHIP, "Ready to ship", f"/features/{feature_id}")


def compute(feature_id: int, *, stage: str, body: Optional[str],
            open_defects: int, blamed_now: int,
            facts: TaskFacts) -> NextStep:
    """R2's `next_step` column. `blamed_now` counts defects blamed against the
    current ship claim — the ones that made the feature escaped."""
    page = f"/features/{feature_id}"
    if stage == events_module.STAGE_SPEC:
        return _spec_step(feature_id, body, facts)
    if stage == events_module.STAGE_BUILDING:
        return _building_step(feature_id, facts, open_defects)
    if stage == events_module.STAGE_ESCAPED:
        return NextStep(
            REOPEN,
            f"{_plural(blamed_now, 'blamed defect')} — reopen to fix", page)
    if stage == events_module.STAGE_ABANDONED:
        return NextStep(REOPEN, "Abandoned — reopen to return to spec", page)
    return NextStep(NONE, "", None)


def _blamed_now(feature_id: int) -> int:
    last = events_module.last(feature_id)
    if last is None or last.kind != events_module.SHIPPED:
        return 0
    return escapes_module.against(last.id)


def _open_defects(feature_id: int) -> list[Defect]:
    with SessionLocal() as session:
        return list(session.exec(
            select(Defect).where(Defect.feature_id == feature_id,
                                 Defect.status == STATUS_OPEN)
            .order_by(Defect.id)).all())


def for_feature(feature_id: int) -> NextStep:
    feature = feature_store.require(feature_id)
    return compute(feature_id, stage=events_module.stage(feature_id),
                   body=feature.body,
                   open_defects=len(_open_defects(feature_id)),
                   blamed_now=_blamed_now(feature_id),
                   facts=task_facts([feature_id])[feature_id])


def _open_tasks(feature_id: int) -> list[AgentTask]:
    with SessionLocal() as session:
        return list(session.exec(
            select(AgentTask).where(AgentTask.feature_id == feature_id,
                                    AgentTask.status.notin_(_CLOSED_TASK))
            .order_by(AgentTask.id)).all())


def _last_sync_kind(feature_id: int) -> Optional[str]:
    with SessionLocal() as session:
        row = session.exec(
            select(FeatureEvent)
            .where(FeatureEvent.feature_id == feature_id,
                   FeatureEvent.declared_by.startswith("sync:"))
            .order_by(FeatureEvent.id.desc())).first()
    return row.kind if row is not None else None


def _tracker_warning(feature) -> Optional[str]:
    if not sync_module.is_remote(feature):
        return None
    said = _last_sync_kind(feature.id)
    if said == events_module.SHIPPED:
        return None
    tracker = registry.display_name(feature.origin)
    state = (f"still says {_TRACKER_WORD[said]}" if said in _TRACKER_WORD
             else "has not been told")
    return (f"{tracker} {state}; the next Refresh will count a walk-back "
            "unless you move it there.")


def ship_check(feature_id: int) -> dict:
    """What a Ship click must show before it appends: whether this stage offers
    ship at all, and the open work the claim would leave behind."""
    feature = feature_store.require(feature_id)
    stage = events_module.stage(feature_id)
    offered = "ship" in feature_store.verbs(stage)
    warnings = []
    if stage == events_module.STAGE_SPEC:
        warnings.append("no build run recorded")
    tracker = _tracker_warning(feature) if offered else None
    if tracker:
        warnings.append(tracker)
    return {
        "allowed": offered,
        "open_tasks": [{"id": t.id, "title": t.title, "status": t.status}
                       for t in _open_tasks(feature_id)],
        "open_defects": [{"id": d.id, "symptom": d.symptom}
                         for d in _open_defects(feature_id)],
        "warnings": warnings,
    }
