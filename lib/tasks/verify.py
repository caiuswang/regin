"""An attempt's `verify_cmd` — the gate between a run ending and its settle.

The command runs in a subprocess of its own, in the tree the attempt ran in,
and its exit code is what the attempt settles on. regin's own reading of the
run — the agent's `task finish` status, or the SDK outcome — is consulted only
when there is no command. That inversion is the whole point: the thing
`verify_cmd` replaces is an agent grading itself.

Nothing here raises. A command that could not be started has not produced a
missing verdict, it has produced a failing one — a verify that did not run has
proved nothing, and letting that read as success would open the gate wider than
having no gate at all.

The subprocess is separate so that a verify which takes down its surroundings
cannot take down the settle with them; it is still *waited on*, because the
status being written is the command's answer. A verify that never returns would
therefore strand the attempt, so the wait is bounded — that ceiling is a
resource bound, not a guess about how long a test suite takes, and expiry is
reported as its own exit code rather than dressed up as a test failure.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Optional

from lib.activity_log import get_activity_logger
from lib.settings import settings

log = get_activity_logger("tasks")

# The shell's own code for a command it could not find. Reused for every way a
# verify fails to start, so "did not run" is never confusable with "passed".
NOT_RUN = 127
# `timeout(1)`'s code for a command it killed.
TIMED_OUT = 124

_REASON_MAX = 200


@dataclass(frozen=True)
class Verdict:
    """What the verify command decided, and how to say it to a human."""

    exit_code: int
    reason: str

    @property
    def passed(self) -> bool:
        return self.exit_code == 0


def run_verify(command: str, *, cwd: Optional[str] = None) -> Verdict:
    """Run `command` to completion and report its exit code. Never raises."""
    ceiling = settings.tasks.verify_timeout_seconds
    try:
        completed = subprocess.run(  # noqa: S602 — the command is operator-authored
            command, shell=True, cwd=cwd, capture_output=True,
            text=True, timeout=ceiling)
    except subprocess.TimeoutExpired:
        log.write("task_verify_timed_out", command=command, cwd=cwd)
        return Verdict(TIMED_OUT, f"verify did not finish within {ceiling}s")
    except OSError as exc:
        log.write("task_verify_did_not_run", command=command, cwd=cwd,
                  error=str(exc))
        return Verdict(NOT_RUN, f"verify did not run: {exc}")
    code = completed.returncode
    log.write("task_verify_ran", command=command, cwd=cwd, exit_code=code)
    if code == 0:
        return Verdict(0, "verify passed")
    return Verdict(code, f"verify failed (exit {code}){_tail(completed)}")


def _tail(completed: subprocess.CompletedProcess) -> str:
    """The last of what the command said, so a failure names itself."""
    output = (completed.stderr or "") or (completed.stdout or "")
    line = output.strip().splitlines()[-1:] or [""]
    return f": {line[0][:_REASON_MAX]}" if line[0] else ""
