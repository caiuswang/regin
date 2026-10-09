"""The spec's slice table, turned into task rows and dependency edges.

Transcription is the second segment: it reads a plan a human already wrote
and approved, and lands it in `agent_tasks` + `agent_task_deps`. It makes no
judgement of its own. A row it cannot read unambiguously is refused by name
rather than guessed at, because a transcriber that filled in a missing Base
would be authoring the plan instead of transcribing it — and the plan's only
carrier is the spec (Invariant 1).

Identity is `(feature_id, "S<n>")`, carried by the `(source, external_id)`
unique constraint the capture path already enforces. No new column, which the
spec forbids — and no title prefix either, because a title is a sentence a
human edits: keying off one means a slice renamed in the UI is transcribed a
second time, and Invariant 2 would hold only for as long as nobody typed.

The proposed arm parameters are *returned*, never written. Arming is a human
act, so a transcriber that persisted one would have armed the slice.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from sqlmodel import select

from lib.activity_log import get_activity_logger
from lib.orm import SessionLocal
from lib.orm.models.tasks import (
    AgentTask, TASK_KIND_BUILD, TASK_STATUS_ARMED, TASK_STATUS_DONE,
    TASK_STATUS_FAILED,
    TASK_STATUS_IDEA, TASK_STATUS_QUEUED, TASK_STATUS_RUNNING,
)
from lib.tasks import deps as deps_module
from lib.tasks import store
from lib.tasks.slice_table import SliceRow, SliceTableError, parse_slice_table

log = get_activity_logger("tasks")

TRANSCRIBE_SOURCE = "spec"

TITLE_JOINER = " — "

# A slice already in flight keeps the parameters it is flying with; a slice
# that has settled keeps its record. Only an idea is still the plan's to move.
UPDATABLE = (TASK_STATUS_IDEA,)
IN_FLIGHT = (TASK_STATUS_ARMED, TASK_STATUS_QUEUED, TASK_STATUS_RUNNING)
SETTLED = (TASK_STATUS_DONE, TASK_STATUS_FAILED)

# What the fourth body section always says. It is written by transcription
# and never by a human: R6 makes merging back a precondition of reporting
# done, so a slice whose author forgot to type it would report done anyway.
CLOSING_ACTION = "合回 `{base}`（这是报告完成的前置条件，不是可选步骤）"

class TranscribeRefused(ValueError):
    """The table cannot be transcribed at all — nothing was written."""

    def __init__(self, message: str, row: Optional[str] = None,
                 column: Optional[str] = None) -> None:
        super().__init__(message)
        self.row = row
        self.column = column


@dataclass
class SliceOutcome:
    """What transcription did with one row, and why."""

    n: str
    action: str            # created | updated | refused | untouched
    task_id: Optional[int] = None
    status: Optional[str] = None
    detail: str = ""
    deps: list[int] = field(default_factory=list)
    # Edges the table retracted that this run did not remove, because the
    # slice was not its to rewrite. Reported rather than silently left: a
    # graph that disagrees with the approved plan is the one thing a reader
    # of `agent_task_deps` cannot detect for itself.
    stale_deps: list[int] = field(default_factory=list)
    arm: Optional[dict] = None


@dataclass
class Transcription:
    feature_id: int
    outcomes: list[SliceOutcome]

    @property
    def refused(self) -> list[SliceOutcome]:
        return [o for o in self.outcomes if o.action == "refused"]

    def to_json_obj(self) -> dict:
        return {
            "feature_id": self.feature_id,
            "slices": [
                {"n": o.n, "action": o.action, "task_id": o.task_id,
                 "status": o.status, "detail": o.detail, "deps": o.deps,
                 "stale_deps": o.stale_deps, "arm": o.arm}
                for o in self.outcomes
            ],
        }


def slice_title(row: SliceRow) -> str:
    return f"{row.n}{TITLE_JOINER}{row.title}"


def slice_key(feature_id: int, n: str) -> str:
    """The `external_id` half of a slice's identity."""
    return f"{feature_id}:{n}"


def slice_of(task: AgentTask) -> Optional[tuple[int, str]]:
    """`(feature_id, "S<n>")` for a transcribed slice task, else None.

    Read off the identity transcription wrote, never off the title: a title
    is a sentence a human edits.
    """
    if task.source != TRANSCRIBE_SOURCE or task.feature_id is None:
        return None
    prefix = f"{task.feature_id}:"
    key = task.external_id or ""
    if not key.startswith(prefix):
        return None
    return task.feature_id, key[len(prefix):]


def default_branch_prefix(feature_id: int) -> str:
    return f"f{feature_id}/"


def slice_body(row: SliceRow) -> str:
    """The four sections of R3. A row missing one never reaches here — the
    parser refuses it as a missing column, which names the column."""
    return "\n".join([
        "## Delivers", "", row.delivers, "",
        "## Spec 依据", "", ", ".join(row.spec_refs), "",
        "## 验收测试名", "",
        *[f"- `{name}`" for name in row.tests], "",
        "## 收尾动作", "", CLOSING_ACTION.format(base=row.base), "",
    ])


def arm_proposal(row: SliceRow, *, branch: str) -> dict:
    """The parameters a human would arm this slice with — proposed, not set.

    `worktree.base` comes from the table and from nowhere else: Invariant 3
    says a slice's base is never the repository's default branch unless the
    table said so, and a default filled in here is exactly how it would be.
    """
    return {
        "worktree": {"branch": branch, "base": row.base},
        "skill": {"name": row.skill},
    }


def _refuse_unknown_and_cyclic(rows: list[SliceRow]) -> None:
    known = {row.n for row in rows}
    for row in rows:
        for dep in row.depends_on:
            if dep not in known:
                raise TranscribeRefused(
                    f"slice {row.n} depends on {dep}, which the table does "
                    "not define", row.n, "depends_on")
            if dep == row.n:
                raise TranscribeRefused(
                    f"slice {row.n} depends on itself", row.n, "depends_on")
    _refuse_cycles(rows)


def _refuse_cycles(rows: list[SliceRow]) -> None:
    edges = {row.n: list(row.depends_on) for row in rows}
    state: dict[str, int] = {}

    def walk(node: str, trail: list[str]) -> None:
        state[node] = 1
        for dep in edges.get(node, ()):
            if state.get(dep) == 1:
                cycle = " → ".join(trail + [node, dep])
                raise TranscribeRefused(
                    f"the slice table has a cycle: {cycle}", node,
                    "depends_on")
            if state.get(dep) is None:
                walk(dep, trail + [node])
        state[node] = 2

    for row in rows:
        if state.get(row.n) is None:
            walk(row.n, [])


def _refuse_two_schema_slices(rows: list[SliceRow]) -> None:
    owners = [row.n for row in rows if row.owns_schema_change]
    if len(owners) > 1:
        raise TranscribeRefused(
            "a schema change belongs to exactly one slice, but "
            + ", ".join(owners) + " each claim one")


def check_graph(rows: list[SliceRow]) -> None:
    """The table-wide refusals transcription raises before any write."""
    _refuse_unknown_and_cyclic(rows)
    _refuse_two_schema_slices(rows)


def existing_slices(feature_id: int,
                    rows: list[SliceRow]) -> dict[str, AgentTask]:
    """The feature's already-transcribed slices, keyed by slice id."""
    keys = {slice_key(feature_id, row.n): row.n for row in rows}
    with SessionLocal() as session:
        found = session.exec(
            select(AgentTask)
            .where(AgentTask.source == TRANSCRIBE_SOURCE)
            .where(AgentTask.external_id.in_(keys))).all()
    return {keys[row.external_id]: row for row in found}


def branch_for(row: SliceRow, branch_prefix: str) -> str:
    return f"{branch_prefix}{row.n.lower()}"


def _write_one(row: SliceRow, existing: Optional[AgentTask], *,
               feature_id: int, branch_prefix: str) -> SliceOutcome:
    title, body = slice_title(row), slice_body(row)
    arm = arm_proposal(row, branch=branch_for(row, branch_prefix))
    if existing is None:
        view, _ = store.capture_task(
            title=title, source=TRANSCRIBE_SOURCE, body=body,
            feature_id=feature_id, kind=TASK_KIND_BUILD,
            external_id=slice_key(feature_id, row.n))
        return SliceOutcome(row.n, "created", view["id"], view["status"],
                            arm=arm)
    if existing.status in IN_FLIGHT:
        return SliceOutcome(
            row.n, "refused", existing.id, existing.status,
            f"slice {row.n} is {existing.status} — its parameters are already "
            "in flight, so the table cannot move it")
    if existing.status in SETTLED:
        return SliceOutcome(
            row.n, "untouched", existing.id, existing.status,
            f"slice {row.n} has settled {existing.status} — not updated")
    # `feature_id` is re-asserted, not assumed: the external id is keyed to
    # this feature, so a slice moved out from under it would otherwise be
    # transcribed here forever while living somewhere else.
    store.update_task(existing.id, {"title": title, "body": body,
                                    "feature_id": feature_id})
    return SliceOutcome(row.n, "updated", existing.id, existing.status,
                        arm=arm)


def _write_edges(rows: list[SliceRow],
                 outcomes: dict[str, SliceOutcome]) -> None:
    """Replace each written slice's edge set with what the table says.

    Only slices this run wrote get edges: a refused slice is mid-flight and a
    settled one is a record, and rewriting either one's dependencies would
    change a graph that is already being read. Every task written here is an
    `idea`, so no readiness recomputation is owed — nothing is armed to fire.

    What that leaves behind is named, not swallowed: an untouched slice whose
    edges the table has since retracted is still gated on them, and only this
    run knows the two disagree.
    """
    for row in rows:
        outcome = outcomes[row.n]
        # `dep` is a `SliceDep`: a slice id that also knows what the edge
        # means, so the table's `S2 (after)` survives into the graph instead
        # of being resolved under the strict rule.
        wanted = [deps_module.DepEdge(outcomes[dep].task_id, dep.kind)
                  for dep in row.depends_on
                  if outcomes[dep].task_id is not None]
        if outcome.action not in ("created", "updated"):
            _report_stale(outcome, [edge.id for edge in wanted])
            continue
        deps_module.set_deps(outcome.task_id, wanted)
        # `set_deps` replaces the whole set, so a re-read of an unchanged table
        # would otherwise put back every ordering the graph has already
        # honoured. Retiring here is what keeps re-transcription idempotent
        # against a feature whose earlier slices have run.
        deps_module.retire_satisfied_after(outcome.task_id)
        outcome.deps = [edge.id for edge in deps_module.dep_edges(
            outcome.task_id)]


def _report_stale(outcome: SliceOutcome, wanted: list[int]) -> None:
    if outcome.task_id is None:
        return
    outcome.deps = deps_module.depends_on(outcome.task_id)
    outcome.stale_deps = sorted(set(outcome.deps) - set(wanted))
    if outcome.stale_deps:
        outcome.detail += (
            "; its edges were left as they are, so it still waits for "
            + ", ".join(f"#{t}" for t in outcome.stale_deps)
            + " which the table no longer declares")


def transcribe(feature_id: int, body: str, *, branch_prefix: str
               ) -> Transcription:
    """Land the spec's slice table as tasks and edges, or refuse wholesale.

    Wholesale refusals — an unknown or cyclic edge, a missing column, two
    slices both changing schema — are raised before any write, so a refused
    table leaves the database exactly as it found it.
    """
    try:
        rows = parse_slice_table(body)
    except SliceTableError as exc:
        raise TranscribeRefused(str(exc), exc.row, exc.column) from exc
    check_graph(rows)

    existing = existing_slices(feature_id, rows)
    outcomes: dict[str, SliceOutcome] = {}
    for row in rows:
        outcomes[row.n] = _write_one(
            row, existing.get(row.n), feature_id=feature_id,
            branch_prefix=branch_prefix)
    _write_edges(rows, outcomes)

    result = Transcription(feature_id, [outcomes[row.n] for row in rows])
    log.write("feature_transcribed", feature_id=feature_id, slices=len(rows),
              refused=len(result.refused))
    return result
