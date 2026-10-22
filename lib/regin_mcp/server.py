"""`regin` — the one MCP server an agent uses to talk to regin's pipeline.

It carries `send_to_user` and the project-management verbs over features,
tasks and defects, so any MCP-speaking agent — Claude Code, CodeBuddy,
WorkBuddy — reads and writes the same pipeline the CLI and the web UI do.

`send_to_user` stays hook-persisted: this process is session-blind, so the
tool only acks and the PostToolUse hook (matching `*__send_to_user`) lands the
message. Started with `--no-send-to-user` it omits that tool, which is how it
is registered for an agent whose hooks regin cannot install — there the ack
would claim a delivery that never happens.

Being session-blind is also why the write tools take `session_id` instead of
guessing one: the server's environment holds whichever session spawned it,
not the caller's.

Regin imports are deferred into each call: the file runs as a script, so a
module-scope `lib.*` import would load the ORM before a single tool is listed.
"""

from __future__ import annotations

import os
import sys
from typing import Optional

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from mcp.server.fastmcp import FastMCP

from lib.agent_messages.mcp_server import send_to_user

SERVER_NAME = "regin"
NO_SEND_TO_USER = "--no-send-to-user"


def _project_id(name: Optional[str]) -> Optional[int]:
    if not name:
        return None
    from sqlmodel import select
    from lib.orm import SessionLocal
    from lib.orm.models.sync import Repo
    with SessionLocal() as session:
        rows = session.exec(select(Repo).where(Repo.is_active == 1)).all()
    match = next((r for r in rows if r.name == name), None)
    if match is None:
        known = ", ".join(sorted(r.name for r in rows)) or "none registered"
        raise ValueError(f"no project named {name!r}. Registered: {known}")
    return match.id


def _feature(feature_id: int):
    from lib.features import store
    row = store.get(feature_id)
    if row is None:
        raise ValueError(f"no feature {feature_id}")
    return row


# List results are wrapped in an object: FastMCP renders a bare list as one
# content block per item, so an empty list reaches a content-only client as
# nothing at all rather than as "no rows".
def _defect_dict(row) -> dict:
    return {"id": row.id, "feature_id": row.feature_id, "symptom": row.symptom,
            "status": row.status, "round": row.round,
            "missing_check": row.missing_check, "opened_at": row.opened_at,
            "fix_task_id": row.fix_task_id}


def pipeline_overview(project: str = "") -> dict:
    """The pipeline at a glance: what is running, what to run next, what needs
    a decision, features in flight, and (unscoped) every project's rollup.

    Args:
        project: A registered project (repo) name to scope to; empty for all.
    """
    from lib.pipeline.overview import overview
    from lib.pipeline.counts import SCOPE_ALL
    project_id = _project_id(project)
    return overview(SCOPE_ALL if project_id is None else project_id)


def feature_list(project: str = "", stage: str = "", query: str = "",
                 limit: int = 50) -> dict:
    """List features with their stage, rounds, task and defect counts.

    Args:
        project: Registered project name to scope to; empty for all.
        stage: One of spec / building / shipped / escaped / abandoned; empty
            lists every live (non-abandoned) feature.
        query: Free-text match on the title.
        limit: Max rows.
    """
    from lib.features import events, index
    if stage and stage not in events.STAGES:
        raise ValueError(f"unknown stage {stage!r}. One of: {', '.join(events.STAGES)}")
    filters = {"project_id": _project_id(project), "stage": stage,
               "q": query, "sort": "activity:desc"}
    rows, matched = index.rows(filters, size=limit)
    return {"total": matched, "features": [r.to_dict() for r in rows]}


def feature_get(feature_id: int) -> dict:
    """One feature in full: its stage, spec (the row's body), tasks and defects.

    Args:
        feature_id: The feature's id.
    """
    from lib.defects import store as defect_store
    from lib.features import events, store
    from lib.tasks.store import serialize
    row = _feature(feature_id)
    return {"id": row.id, "title": row.title, "stage": events.stage(row.id),
            "project": store.project_name(row.project_id),
            "spec": row.body or "",
            "tasks": [{"id": t["id"], "title": t["title"], "status": t["status"]}
                      for t in map(serialize, store.tasks_of(row.id))],
            "defects": [_defect_dict(d) for d in defect_store.for_feature(row.id)]}


def feature_create(title: str, spec: str = "", project: str = "",
                   session_id: str = "") -> dict:
    """Declare a feature: the row and its `specced` event, in one transaction.

    Args:
        title: What is being delivered.
        spec: The spec — thin prose stating intent and acceptance, no file paths.
        project: Registered project name to file it under.
        session_id: Your session's id, recorded as the run that minted it.
    """
    from lib.features import events, store
    row = store.create(title, body=spec or None, trace_id=session_id or None,
                       project_id=_project_id(project))
    return {"id": row.id, "title": row.title, "stage": events.stage(row.id)}


def feature_set_spec(feature_id: int, spec: str) -> dict:
    """Replace a feature's spec. The spec lives on the row and nowhere else.

    Args:
        feature_id: The feature's id.
        spec: The full new spec; it replaces the old one, it is not appended.
    """
    from lib.features import store
    _feature(feature_id)
    if not spec.strip():
        raise ValueError("the spec given is empty — nothing written")
    row = store.set_body(feature_id, spec)
    return {"id": row.id, "spec_chars": len(row.body or "")}


def task_list(status: str = "idea", limit: int = 50) -> dict:
    """Tasks newest first.

    Args:
        status: Filter by status (idea, running, done, …); empty for all.
        limit: Max rows.
    """
    from lib.tasks import store
    return {"tasks": store.list_tasks(status=status or None, limit=limit)}


def task_get(task_id: int) -> dict:
    """One task in full.

    Args:
        task_id: The task's id.
    """
    from lib.tasks import store
    task = store.get_task(task_id)
    if task is None:
        raise ValueError(f"no task #{task_id}")
    return task


def task_create(title: str, body: str = "", project: str = "",
                feature_id: int = 0, priority: str = "none",
                kind: str = "task") -> dict:
    """Capture a task, optionally as a step of a feature.

    Args:
        title: One-line title (≤200 chars).
        body: Longer description.
        project: Registered project name; with `feature_id` set it defaults to
            the feature's project.
        feature_id: The feature this task is a step of; 0 for none.
        priority: none / low / medium / high / urgent.
        kind: What the step is — task, spec, build or fix.
    """
    from lib.orm.models.tasks import TASK_KINDS, TASK_PRIORITIES
    from lib.tasks import store
    if not title.strip():
        raise ValueError("a task needs a title")
    if priority not in TASK_PRIORITIES:
        raise ValueError(f"unknown priority {priority!r}. One of: {', '.join(TASK_PRIORITIES)}")
    if kind not in TASK_KINDS:
        raise ValueError(f"unknown kind {kind!r}. One of: {', '.join(TASK_KINDS)}")
    extra = {}
    if feature_id:
        _feature(feature_id)
        extra["feature_id"] = feature_id
    if project:
        extra["project_id"] = _project_id(project)
    task, _ = store.capture_task(title=title[:200], body=body or None,
                                 source="mcp", priority=priority, kind=kind,
                                 **extra)
    return task


def defect_open(symptom: str, feature_id: int = 0, session_id: str = "",
                paths: Optional[list[str]] = None) -> dict:
    """Record a defect: something a feature got wrong.

    Re-reporting the same symptom returns the existing row with
    `deduped: true` rather than opening a second one.

    Args:
        symptom: What went wrong, in your words.
        feature_id: The feature that CAUSED the bug (not necessarily the one
            you are working on); 0 leaves it unattributed — prefer that over
            a guess.
        session_id: Your session's id, where this was reported.
        paths: Suspect files — a hint, not a claim.
    """
    from lib.defects import store
    result = store.record_reporting(symptom, feature_id=feature_id or None,
                                    session_id=session_id or None,
                                    suspect_paths=paths or ())
    return {**_defect_dict(result.defect), "deduped": result.deduped}


def defect_list(feature_id: int = 0, whole_ledger: bool = False) -> dict:
    """List defects: one feature's, the whole ledger, or the unattributed ones.

    Args:
        feature_id: A feature's defects; 0 for no feature filter.
        whole_ledger: With no feature, the whole ledger newest first; otherwise only
            the defects awaiting attribution.
    """
    from lib.defects import store
    if feature_id:
        rows = store.for_feature(_feature(feature_id).id)
    else:
        rows = store.every() if whole_ledger else store.unattributed()
    return {"defects": [_defect_dict(r) for r in rows]}


def defect_close(defect_id: int, check: str) -> dict:
    """Close a defect, naming the check that would have caught it.

    Args:
        defect_id: The defect's id.
        check: The test or check that would have caught this bug.
    """
    from lib.defects import store
    return _defect_dict(store.close(defect_id, missing_check=check))


PIPELINE_TOOLS = (pipeline_overview, feature_list, feature_get, feature_create,
                  feature_set_spec, task_list, task_get, task_create,
                  defect_open, defect_list, defect_close)


def build_server(*, with_send_to_user: bool = True) -> FastMCP:
    server = FastMCP(SERVER_NAME)
    if with_send_to_user:
        server.add_tool(send_to_user)
    for tool in PIPELINE_TOOLS:
        server.add_tool(tool)
    return server


if __name__ == "__main__":
    build_server(with_send_to_user=NO_SEND_TO_USER not in sys.argv[1:]).run()
