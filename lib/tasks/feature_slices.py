"""A feature's slice table, as the operations a browser can ask for.

Each function here is the one the CLI verb of the same name reaches, so a
slice transcribed from the ledger and one transcribed from a terminal land
the same rows (feature #119 I1). Nothing is decided here that the modules
below it do not already decide: the table is parsed by `slice_table`,
refused by `slicing` and `transcribe`, tailed by `tail`, armed by `arm` +
`store.arm_task`.

Arm parameters for a slice are *derived at read time* from its table row and
never stored (#119 D7): the table is the plan, and a copy kept on the task
would drift the first time someone edited the spec.
"""

from __future__ import annotations

import os
from typing import Optional

from lib.activity_log import get_activity_logger
from lib.features import store as feature_store
from lib.features import sync as sync_module
from lib.orm import SessionLocal
from lib.orm.models.features import Feature
from lib.orm.models.sync import Repo
from lib.orm.models.tasks import AgentTask, TASK_STATUS_IDEA
from lib.settings import settings
from lib.tasks import arm as arm_module
from lib.tasks import runs, store
from lib.tasks.capture import CaptureRejected
from lib.tasks.commands import CommandsError, load_project_commands
from lib.tasks.slice_table import SliceRow, SliceTableError, parse_slice_table
from lib.tasks.slicing import SlicingRefusal, append_rows, preview
from lib.tasks.tail import has_tail, tail_slice_rows
from lib.tasks.transcribe import (
    Transcription, TranscribeRefused, arm_proposal, branch_for, check_graph,
    default_branch_prefix, existing_slices, slice_of, transcribe,
)

log = get_activity_logger("tasks")


class SlicesRefused(ValueError):
    """The feature's state refuses the verb — the request itself was sound."""


class SlicesRejected(ValueError):
    """The request cannot be served as asked: a table that does not parse, a
    project with no command table, a malformed item list."""


class NoProposal(LookupError):
    """The task has no slice row to derive arm parameters from."""


def _rows_or_empty(body: str) -> list[SliceRow]:
    try:
        return parse_slice_table(body)
    except SliceTableError:
        return []


def _refusal(body: str, rows: list[SliceRow]) -> Optional[dict]:
    _, refused = preview(body)
    if refused is not None:
        return {"message": str(refused), "missing": list(refused.missing)}
    try:
        check_graph(rows)
    except TranscribeRefused as exc:
        return {"message": str(exc), "missing": [str(exc)]}
    return None


def report(feature_id: int) -> dict:
    """The feature's `SlicesReport`: whether its spec slices, and each row
    beside the task transcription made of it.

    The rows are read even when slicing is refused for want of an acceptance
    section, so a table the author has already written stays visible while
    the reason it cannot be transcribed is fixed.
    """
    feature = feature_store.require(feature_id)
    body = feature.body or ""
    rows = _rows_or_empty(body)
    refusal = _refusal(body, rows)
    tasks = existing_slices(feature_id, rows) if rows else {}
    return {
        "sliceable": refusal is None,
        "refusal": refusal,
        "tail_missing": bool(rows) and not has_tail(rows),
        "rows": [_row_view(row, tasks.get(row.n)) for row in rows],
    }


def _row_view(row: SliceRow, task: Optional[AgentTask]) -> dict:
    return {
        "n": row.n, "title": row.title,
        "depends_on": [str(dep) for dep in row.depends_on],
        "base": row.base, "skill": row.skill, "verify": row.verify_cmd,
        "task_id": task.id if task is not None else None,
        "task_status": task.status if task is not None else None,
    }


def _repo_root(feature: Feature) -> str:
    """Where the feature's command table lives: its project's repo, else
    this install's own checkout."""
    if feature.project_id is not None:
        with SessionLocal() as session:
            repo = session.get(Repo, feature.project_id)
        if repo is not None and repo.path:
            return repo.path
    return os.fspath(settings.project_root)


def default_tail_base(feature_id: int) -> str:
    return f"f{feature_id}/integration"


def append_tail(feature_id: int, *, base: Optional[str] = None,
                repo_root: Optional[str] = None) -> list[SliceRow]:
    """Write the chain tail into the spec's table, once.

    `regin task tail` prints the same rows for a human to paste; this is the
    paste. It edits the spec, so a tracked feature — whose spec is the
    tracker's (#119 D4) — is refused rather than written behind the tracker's
    back and overwritten by the next Refresh.
    """
    feature = feature_store.require(feature_id)
    if sync_module.is_remote(feature):
        raise SlicesRefused(
            f"feature #{feature_id}'s spec lives in its tracker — add the "
            "tail rows there, then Refresh")
    body = feature.body or ""
    try:
        rows = parse_slice_table(body)
    except SliceTableError as exc:
        raise SlicesRejected(str(exc)) from exc
    if has_tail(rows):
        raise SlicesRefused(
            f"feature #{feature_id}'s table already ends in a chain tail")
    try:
        commands = load_project_commands(repo_root or _repo_root(feature))
    except CommandsError as exc:
        raise SlicesRejected(
            f"{exc} — every command in the tail is read from the project's "
            "command table; run `regin task commands` for a draft") from exc
    tail = tail_slice_rows(rows, commands, base or default_tail_base(feature_id))
    try:
        appended = append_rows(body, tail)
    except SlicingRefusal as exc:
        raise SlicesRejected(str(exc)) from exc
    feature_store.set_body(feature_id, appended)
    log.write("tail_appended", feature_id=feature_id,
              slices=",".join(row.n for row in tail))
    return tail


def transcribe_feature(feature_id: int, *,
                       branch_prefix: Optional[str] = None) -> Transcription:
    """`regin task transcribe`, for any caller."""
    feature = feature_store.require(feature_id)
    if not (feature.body or "").strip():
        raise SlicesRefused(
            f"feature #{feature_id} has no spec — the slice table is the plan "
            "and there is nowhere else to read it from")
    return transcribe(
        feature_id, feature.body,
        branch_prefix=branch_prefix or default_branch_prefix(feature_id))


def transcription_summary(result: Transcription) -> dict:
    by_action: dict[str, list] = {"created": [], "updated": []}
    refused = []
    for outcome in result.outcomes:
        if outcome.action in by_action:
            by_action[outcome.action].append(outcome.task_id)
        elif outcome.action == "refused":
            refused.append({"n": outcome.n, "reason": outcome.detail})
    return {**by_action, "refused": refused,
            "slices": result.to_json_obj()["slices"]}


def _get_task(task_id: int) -> Optional[AgentTask]:
    with SessionLocal() as session:
        return session.get(AgentTask, task_id)


def proposal_for_task(task_id: int) -> dict:
    """The arm parameters the task's slice row proposes, read now.

    `NoProposal` when the task is not a transcribed slice, or its row has
    left the table since — a proposal for a row the plan no longer carries
    would be a plan nobody wrote.
    """
    task = _get_task(task_id)
    ident = slice_of(task) if task is not None else None
    if ident is None:
        raise NoProposal(f"task #{task_id} is not a transcribed slice")
    feature_id, n = ident
    feature = feature_store.get(feature_id)
    rows = _rows_or_empty(feature.body or "") if feature is not None else []
    row = next((r for r in rows if r.n == n), None)
    if row is None:
        raise NoProposal(
            f"feature #{feature_id}'s slice table no longer has a row {n}")
    proposed = arm_proposal(
        row, branch=branch_for(row, default_branch_prefix(feature_id)))
    return {"branch": proposed["worktree"]["branch"],
            "base": proposed["worktree"]["base"],
            "skill": proposed["skill"]["name"],
            "verify_cmd": proposed["verify_cmd"]}


def arm_slices(feature_id: int, items) -> dict:
    """Arm each listed idea slice with its own parameters. Never runs one.

    Per item, not all-or-nothing: a refused row is named with its reason and
    the rest are armed, so the operator fixes one row rather than re-sending
    every other. Arming does call `fire_if_ready`, exactly as a single arm
    does — a slice whose dependencies already landed is released by its edge,
    which is the grant — but a slice with no dependencies is never started.
    """
    feature_store.require(feature_id)
    if not isinstance(items, list) or not items:
        raise SlicesRejected("items must be a non-empty list")
    armed: list[int] = []
    refused: list[dict] = []
    for item in items:
        task_id = item.get("task_id") if isinstance(item, dict) else None
        reason = _arm_one(feature_id, task_id, item)
        if reason is None:
            armed.append(task_id)
        else:
            refused.append({"task_id": task_id, "reason": reason})
    log.write("slices_armed", feature_id=feature_id, armed=len(armed),
              refused=len(refused))
    return {"armed": armed, "refused": refused}


def _arm_refusal(feature_id: int, task_id) -> Optional[str]:
    if not isinstance(task_id, int) or isinstance(task_id, bool):
        return "task_id must be an integer"
    task = _get_task(task_id)
    if task is None:
        return f"no task #{task_id}"
    ident = slice_of(task)
    if ident is None or ident[0] != feature_id:
        return f"task #{task_id} is not a slice of feature #{feature_id}"
    if task.status != TASK_STATUS_IDEA:
        return (f"{ident[1]} is {task.status} — only an idea slice is armed "
                "here; re-arm it from its task page")
    return None


def _arm_one(feature_id: int, task_id, item) -> Optional[str]:
    refusal = _arm_refusal(feature_id, task_id)
    if refusal is not None:
        return refusal
    try:
        fields = arm_module.parse_arm(
            {k: v for k, v in item.items() if k != "task_id"})
    except CaptureRejected as exc:
        return str(exc)
    try:
        store.arm_task(task_id, **fields)
    except store.TransitionRejected as exc:
        return str(exc)
    try:
        runs.fire_if_ready(task_id)
    except Exception:  # noqa: BLE001 — a launch fault is not a failed arm
        log.error("task_fire_failed", task_id=task_id, exc_info=True)
    return None
