"""Shared helpers for the repo-root test fixtures.

These live outside `conftest.py` because a bare `import conftest` resolves
to whichever conftest is nearest on `sys.path` — from `tests/` that is
`tests/conftest.py`, not the root one — so tests asserting on the spawn
guard could not import it reliably.
"""

from __future__ import annotations

import subprocess


def spawning_modules():
    """Every module that launches a configured external agent.

    Kept in one place because the guard and its opt-out must cover exactly
    the same set — an entry present in one and missing from the other is a
    hole. `proposal_external` is the primary drafting launcher and was the
    one originally missed.
    """
    from lib.grader import adapters as grader_adapters
    from lib.memory import adapters as memory_adapters
    from lib.topics import proposal_external, proposal_review
    return (memory_adapters, grader_adapters, proposal_review, proposal_external)


class ExternalAgentSpawnBlocked(BaseException):
    """Raised when a test tries to launch a real external agent.

    Deliberately a `BaseException`, not an `Exception`. Two of the guarded
    call sites catch broadly — `lib/topics/proposal_review.py:456` is a bare
    `except Exception` — which would swallow an `AssertionError` and let the
    run report itself as "agent unavailable", the exact silent degradation
    the guard exists to make impossible. Deriving from `BaseException` means
    the guard escapes those handlers while pytest still fails the test.
    """


class SpawnGuard:
    """Drop-in for the `subprocess` module that refuses to launch an agent.

    Delegates every other attribute (`PIPE`, `TimeoutExpired`, …) to the real
    module, so the guarded call sites' exception handling still resolves.
    """

    def __getattr__(self, name):
        return getattr(subprocess, name)

    @staticmethod
    def _refuse(cmd, *_args, **_kwargs):
        raise ExternalAgentSpawnBlocked(
            f"test attempted to spawn an external agent: {cmd!r}. "
            "Stub the provider seam instead — a real spawn runs a coding "
            "agent in the working tree and costs API credit."
        )

    run = _refuse
    Popen = _refuse
    call = _refuse
    check_call = _refuse
    check_output = _refuse
