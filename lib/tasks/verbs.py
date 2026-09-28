"""Which verbs a task offers, by state — feature #119 R4a, stated once.

| State                | Primary        | Menu                                                     |
|----------------------|----------------|----------------------------------------------------------|
| idea                 | arm            | edit · deps · mark_done · archive · delete               |
| armed, deps clear    | run            | disarm · arm · edit · deps · mark_done · archive · delete |
| armed, blocked       | — (waits on)   | disarm · arm · deps · edit · archive · delete            |
| running              | watch (+ stop) | interrupt                                                |
| failed               | retry          | arm · mark_done · archive · delete                       |
| done                 | —              | reopen · archive                                         |
| archived             | —              | restore · delete                                         |

Every surface that draws a task row reads `primary` and `menu` off the row,
so no Vue component decides what a state may do. `arm` in a menu is Re-arm.
`queued` offers nothing: nothing writes that status (feature #119 D5).
"""

from __future__ import annotations

from typing import Optional

from lib.orm.models.tasks import (
    TASK_STATUS_ARCHIVED, TASK_STATUS_ARMED, TASK_STATUS_DONE,
    TASK_STATUS_FAILED, TASK_STATUS_IDEA, TASK_STATUS_RUNNING,
)

ARM = "arm"
RUN = "run"
RETRY = "retry"
WATCH = "watch"
STOP = "stop"
INTERRUPT = "interrupt"
DISARM = "disarm"
MARK_DONE = "mark_done"
REOPEN = "reopen"
EDIT = "edit"
DEPS = "deps"
ARCHIVE = "archive"
RESTORE = "restore"
DELETE = "delete"

_BLOCKED = "armed_blocked"

_PRIMARY: dict[str, Optional[str]] = {
    TASK_STATUS_IDEA: ARM,
    TASK_STATUS_ARMED: RUN,
    _BLOCKED: None,
    TASK_STATUS_RUNNING: WATCH,
    TASK_STATUS_FAILED: RETRY,
    TASK_STATUS_DONE: None,
    TASK_STATUS_ARCHIVED: None,
}

_MENU: dict[str, tuple[str, ...]] = {
    TASK_STATUS_IDEA: (EDIT, DEPS, MARK_DONE, ARCHIVE, DELETE),
    TASK_STATUS_ARMED: (DISARM, ARM, EDIT, DEPS, MARK_DONE, ARCHIVE, DELETE),
    _BLOCKED: (DISARM, ARM, DEPS, EDIT, ARCHIVE, DELETE),
    TASK_STATUS_RUNNING: (INTERRUPT,),
    TASK_STATUS_FAILED: (ARM, MARK_DONE, ARCHIVE, DELETE),
    TASK_STATUS_DONE: (REOPEN, ARCHIVE),
    TASK_STATUS_ARCHIVED: (RESTORE, DELETE),
}


def _state(status: str, blocked_by: list[int]) -> str:
    if status == TASK_STATUS_ARMED and blocked_by:
        return _BLOCKED
    return status


def primary(status: str, blocked_by: list[int]) -> Optional[str]:
    """The one visible action. None for a settled task, or one waiting on a
    dependency — its `waits_on` is what the row shows instead."""
    return _PRIMARY.get(_state(status, blocked_by))


def menu(status: str, blocked_by: list[int]) -> list[str]:
    """The ⋯ menu, in the order it is drawn."""
    return list(_MENU.get(_state(status, blocked_by), ()))
