"""A build slice reported done must have written the tests it is named for.

The slice's task body lists its acceptance names under `## 验收测试名`
(`transcribe.slice_body`). Finishing it `done` while one of those names
exists nowhere in the run's checkout is a claim nothing can verify, so it
is refused before the report is written. Only existence is checked — the
verify tail runs them. Tail rows (integrate / migrate / verify) re-list
names other slices own and are not held to this.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from lib.agent_sdk import worktree
from lib.orm import SessionLocal
from lib.orm.models.tasks import AgentTask, AgentTaskRun, TASK_KIND_BUILD
from lib.settings import settings
from lib.specs.markdown import section_items
from lib.specs.test_index import build_test_index
from lib.tasks import store
from lib.tasks.tail import TAIL_TESTS
from lib.tasks.transcribe import slice_of

NAMED_TESTS_HEADING = "验收测试名"


def _load(task_id: int, run_id: int) -> tuple[Optional[AgentTask], Optional[AgentTaskRun]]:
    with SessionLocal() as session:
        return session.get(AgentTask, task_id), session.get(AgentTaskRun, run_id)


def named_tests(body: str) -> list[str]:
    return list(dict.fromkeys(section_items(body or "", NAMED_TESTS_HEADING)))


class CheckoutGone(LookupError):
    """The run's worktree is no longer registered, so its tests cannot be read."""


def run_checkout(run: AgentTaskRun) -> Path:
    """Where the run actually worked: its worktree when it had one, else its cwd.

    A worktree run whose tree is gone raises rather than falling back to the
    cwd: the main checkout does not hold the work that ran, and judging the
    finish against it would pass or refuse on someone else's files.
    """
    params = store._decode_params(run.params_json) or {}
    cwd = params.get("cwd") or str(settings.project_root)
    branch = ((params.get("worktree") or {}).get("branch") or "").strip()
    if not branch:
        return Path(cwd)
    try:
        found = worktree.existing_worktree(cwd, branch)
    except (worktree.WorktreeRefused, OSError):
        found = None
    if found is None:
        raise CheckoutGone(
            f"the run's worktree for branch {branch!r} is no longer registered, "
            "so its tests cannot be checked")
    return Path(found["path"])


def missing_tests(task_id: int, run_id: int) -> tuple[list[str], Optional[Path]]:
    """`(names absent from the run's checkout, the checkout searched)`.

    Empty when the task is not a non-tail build slice, or names no tests.
    """
    task, run = _load(task_id, run_id)
    if task is None or run is None or slice_of(task) is None:
        return [], None
    if task.kind != TASK_KIND_BUILD:
        return [], None
    names = named_tests(task.body or "")
    if not names or TAIL_TESTS.intersection(names):
        return [], None
    root = run_checkout(run)
    index = build_test_index(root)
    return [name for name in names if name not in index], root
