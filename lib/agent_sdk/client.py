"""The only module in regin that imports `claude_agent_sdk`.

Keeping the raw SDK behind one door means the launch path can't quietly diverge
between tests and production, and swapping SDK versions touches one file.

The load-bearing detail here is `cli_path`. The SDK ships its own `claude`
inside a platform-specific package and spawns that by default. regin exists to
observe the user's own Claude Code, so a session it launches must run the user's
binary — otherwise traces record a build that was never installed, with
different settings, plugins and version behaviour.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field

from lib.activity_log import get_activity_logger
from lib.settings import settings

log = get_activity_logger("agent_sdk")


class ClaudeCliNotFound(RuntimeError):
    """No `claude` on PATH and no explicit `agent_sdk.cli_path`."""


@dataclass(frozen=True)
class RunOptions:
    """Per-run overrides for a session regin launches on its own behalf.

    A run regin makes for itself talks to its agent through the environment,
    and may want a different model or permission mode than the global defaults
    an operator's session uses. `env` is an *overlay*: the SDK merges it over
    the parent process environment, so it carries only what the run adds.
    """

    env: dict[str, str] = field(default_factory=dict)
    permission_mode: str = ""
    model: str = ""


def _run_overrides(run: RunOptions | None) -> dict:
    """A run's contributions to `ClaudeAgentOptions`, if any."""
    if run is None:
        return {}
    overrides = {}
    if run.env:
        overrides["env"] = dict(run.env)
    if run.permission_mode:
        overrides["permission_mode"] = run.permission_mode
    if run.model:
        overrides["model"] = run.model
    return overrides


def resolve_cli_path() -> str:
    """Absolute path to the `claude` the user actually installed."""
    configured = (settings.agent_sdk.cli_path or "").strip()
    if configured:
        return configured
    found = shutil.which("claude")
    if not found:
        raise ClaudeCliNotFound(
            "Install Claude Code (https://github.com/anthropics/claude-code) "
            "and ensure `claude` is on PATH, or set agent_sdk.cli_path."
        )
    return found


def build_options(*, cwd: str | None = None, can_use_tool=None,
                  resume: str | None = None,
                  options: RunOptions | None = None):
    """`ClaudeAgentOptions` for a regin-launched session.

    `setting_sources` is explicit so the launched agent loads the same
    user/project/local settings and CLAUDE.md an interactive session would —
    without it the SDK starts from a bare configuration and the run behaves
    unlike every other session regin has traced.

    `options` wins over the global settings: those are the defaults for an
    operator-launched session, not a ceiling on what regin's own spawns may
    ask for.
    """
    from claude_agent_sdk import ClaudeAgentOptions

    kwargs = {
        "cli_path": resolve_cli_path(),
        "setting_sources": ["user", "project", "local"],
        "permission_mode": settings.agent_sdk.permission_mode or "default",
        "can_use_tool": can_use_tool,
    }
    if cwd:
        kwargs["cwd"] = cwd
    if resume:
        kwargs["resume"] = resume
    if settings.agent_sdk.model:
        kwargs["model"] = settings.agent_sdk.model
    kwargs.update(_run_overrides(options))
    return ClaudeAgentOptions(**kwargs)


def new_client(**kwargs):
    """A connected-on-enter `ClaudeSDKClient`."""
    from claude_agent_sdk import ClaudeSDKClient

    return ClaudeSDKClient(options=build_options(**kwargs))


def allow(updated_input: dict):
    """The SDK's allow-with-rewritten-input permission result.

    Returning a modified `updated_input` is how a host answers an interactive
    tool: the CLI runs the tool with the fields the operator supplied.
    """
    from claude_agent_sdk import PermissionResultAllow

    return PermissionResultAllow(updated_input=updated_input)


def deny(message: str):
    from claude_agent_sdk import PermissionResultDeny

    return PermissionResultDeny(message=message)
