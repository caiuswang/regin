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

import os
import shutil
from dataclasses import dataclass, field

from lib.activity_log import get_activity_logger
from lib.agent_bridge import child_env
from lib.settings import settings

log = get_activity_logger("agent_sdk")


class ClaudeCliNotFound(RuntimeError):
    """No `claude` on PATH and no explicit `agent_sdk.cli_path`."""


# The SDK's `PermissionMode` values, restated rather than imported: the launch
# surface has to validate an operator's choice on an install where the optional
# `[agent-sdk]` extra isn't present, and a mode the CLI rejects should fail as a
# bad request rather than as a crashed run.
PERMISSION_MODES = ('default', 'acceptEdits', 'plan', 'bypassPermissions',
                    'dontAsk', 'auto')

# The SDK's `EffortLevel`, restated for the same reason as `PERMISSION_MODES`.
EFFORT_LEVELS = ('low', 'medium', 'high', 'xhigh', 'max')

# The CLI's model *aliases*, offered as the launch sheet's menu. Aliases rather
# than pinned ids on purpose: an alias keeps meaning the current build of that
# tier as the install updates, whereas a hardcoded `claude-opus-4-8` would name
# a model this CLI may no longer serve. Not a closed set — the launch route
# accepts any string, so an operator can still pin an exact id (that is what
# the sheet's `custom…` entry is for); this list only decides what is one tap
# away.
MODEL_CHOICES = ('opus', 'sonnet', 'haiku', 'fable')


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
    effort: str = ""


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
    if run.effort:
        overrides["effort"] = run.effort
    return overrides


def resolve_cli_path() -> str:
    """Absolute path to the `claude` the user actually installed.

    `~` is expanded here rather than at the settings layer: `cli_path` is typed
    into the settings UI by hand, where a tilde path is the natural thing to
    write, and an unexpanded one saves fine and only fails at launch.
    """
    configured = (settings.agent_sdk.cli_path or "").strip()
    if configured:
        return os.path.expanduser(configured)
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

    `fork_session` is pinned rather than left to the SDK's default: forking
    gives the resumed session a NEW child id, which would strand the trace the
    run's first half was recorded under — the two halves would render as
    unrelated sessions. regin's own default has to survive a change in the
    SDK's.
    """
    from claude_agent_sdk import ClaudeAgentOptions

    kwargs = {
        "cli_path": resolve_cli_path(),
        "setting_sources": ["user", "project", "local"],
        "permission_mode": settings.agent_sdk.permission_mode or "default",
        "can_use_tool": can_use_tool,
        "fork_session": False,
    }
    if cwd:
        kwargs["cwd"] = cwd
    if resume:
        kwargs["resume"] = resume
    if settings.agent_sdk.model:
        kwargs["model"] = settings.agent_sdk.model
    # Only set when actually chosen: `effort` is newer than the rest of this
    # kwarg set, so an install on an older `claude-agent-sdk` keeps launching
    # unless someone opts in — at which point a hard TypeError naming the
    # option beats a silently ignored choice.
    if settings.agent_sdk.effort:
        kwargs["effort"] = settings.agent_sdk.effort
    kwargs.update(_run_overrides(options))
    # `env` is an overlay the SDK merges over the parent environment, and the
    # bridge flag is applied last: a run's own options may set anything else,
    # but not hand a regin-launched child the operator's pane.
    kwargs["env"] = child_env(kwargs.get("env", {}))
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
