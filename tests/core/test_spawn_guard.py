"""The repo-root spawn guard actually blocks every agent launcher.

Written after an adversarial review found two holes in the first version:
`lib/topics/proposal_external.py` was not guarded at all, and the guard
raised `AssertionError`, which `proposal_review`'s bare `except Exception`
swallowed — the run then reported "agent unavailable" and the test passed.
"""

from __future__ import annotations

import subprocess

import pytest

from conftest_support import ExternalAgentSpawnBlocked, spawning_modules


ALL_LAUNCHERS = [
    "lib.memory.adapters",
    "lib.grader.adapters",
    "lib.topics.proposal_review",
    "lib.topics.proposal_external",
]


@pytest.mark.parametrize("module_name", ALL_LAUNCHERS)
def test_every_launcher_module_is_guarded(module_name):
    guarded = {m.__name__ for m in spawning_modules()}
    assert module_name in guarded


@pytest.mark.parametrize("module_name", ALL_LAUNCHERS)
def test_guarded_module_cannot_spawn(module_name):
    """The autouse fixture has already swapped `subprocess` in each module."""
    import importlib
    module = importlib.import_module(module_name)
    with pytest.raises(ExternalAgentSpawnBlocked):
        module.subprocess.run(["echo", "should-never-run"])
    with pytest.raises(ExternalAgentSpawnBlocked):
        module.subprocess.Popen(["echo", "should-never-run"])


def test_block_survives_a_bare_except_exception():
    """`proposal_review` wraps its spawn in `except Exception`. An
    `AssertionError` would be caught there and silently downgraded, so the
    guard must raise something outside the `Exception` hierarchy."""
    assert not issubclass(ExternalAgentSpawnBlocked, Exception)
    assert issubclass(ExternalAgentSpawnBlocked, BaseException)

    from lib.topics import proposal_review
    try:
        proposal_review.subprocess.Popen(["echo", "x"])
    except Exception:  # noqa: BLE001 - mirrors the real call site
        pytest.fail("guard was swallowed by a bare `except Exception`")
    except ExternalAgentSpawnBlocked:
        pass


def test_guard_delegates_other_subprocess_attributes():
    """Call sites reference `subprocess.PIPE` / `subprocess.SubprocessError`
    in their argument lists and except clauses; the guard must not break
    those."""
    from lib.memory import adapters
    assert adapters.subprocess.PIPE is subprocess.PIPE
    assert adapters.subprocess.SubprocessError is subprocess.SubprocessError
    assert not issubclass(ExternalAgentSpawnBlocked,
                          adapters.subprocess.SubprocessError)


def test_opt_out_restores_real_subprocess(allow_subprocess_spawn):
    from lib.memory import adapters
    assert adapters.subprocess is subprocess
    out = adapters.subprocess.run(["echo", "ok"], capture_output=True)
    assert out.stdout.strip() == b"ok"


def test_configuring_an_agent_does_not_defeat_the_guard(monkeypatch):
    """Layer 1 (empty agent config) is overridden by any test that sets its
    own agent — several fixtures do. Layer 2 must still hold."""
    from lib.settings import TopicProposalExternalAgent, settings
    monkeypatch.setattr(settings, "topic_proposal_external_agents", {
        "claude": TopicProposalExternalAgent(command="echo"),
    })
    from lib.memory.adapters import ExternalAgentLLM
    with pytest.raises(ExternalAgentSpawnBlocked):
        ExternalAgentLLM().complete("prompt")
