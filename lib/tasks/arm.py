"""Validate an arm payload into the execution parameters a task may carry.

Arming is the privileged step: it is the only place an execution parameter
enters `agent_tasks`, which is what preserves the capture invariant — a
captured task arrives inert, and an authenticated human makes it runnable.

The allowlist mirrors `supervisor.launch_run`'s signature, so a field this
module accepts is a field the launch actually honors; anything else is a
rejection naming it rather than a silent drop. `permission_mode` and `effort`
are checked against the CLI's own vocabulary here for the same reason
`web/blueprints/agent_runs.py` checks them: an unknown value would surface much
later as a run that died on start.
"""

from __future__ import annotations

import re
from typing import Optional

from lib.agent_sdk import client
from lib.providers import registry
from lib.tasks.capture import CaptureRejected

ARM_FIELDS = frozenset({
    "cwd", "model", "permission_mode", "effort", "one_shot", "worktree",
    "env", "verify_cmd", "autorun", "skill", "launch", "provider",
})

LAUNCH_SDK, LAUNCH_TMUX = "sdk", "tmux"
LAUNCH_MODES = (LAUNCH_SDK, LAUNCH_TMUX)

# The subset handed to `launch_run`; the rest are regin's own bookkeeping.
LAUNCH_FIELDS = ("cwd", "model", "permission_mode", "effort", "one_shot",
                 "env", "worktree")

_ID_MAX = 200
_PATH_MAX = 2000
_CMD_MAX = 2000
_SKILL_ARGS_MAX = 2000
# A run opens with one slash command; the rest are named for it to invoke, and
# a list long enough to need more than this is a prompt, not a selection.
SKILL_ALSO_MAX = 8
# A slash-command name as the CLI resolves it: `goal-verified`, or
# `plugin:skill` for a namespaced one. Anything else would be sent as a
# prompt beginning with a slash that no command answers.
_SKILL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
# An overlay on the server's own environment, not a payload: bounded so a stray
# body cannot be shipped into a child process.
ENV_MAX_KEYS = 20
_ENV_KEY_MAX = 100
_ENV_VALUE_MAX = 2000
_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _string(payload: dict, key: str, limit: int) -> str:
    value = payload.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise CaptureRejected(f"{key} must be a string", key)
    value = value.strip()
    if len(value) > limit:
        raise CaptureRejected(f"{key} exceeds {limit} characters", key)
    return value


def _choice(payload: dict, key: str, allowed) -> str:
    value = _string(payload, key, _ID_MAX)
    if value and value not in allowed:
        raise CaptureRejected(f"unknown {key} {value!r}", key)
    return value


def _flag(payload: dict, key: str) -> bool:
    value = payload.get(key, False)
    if not isinstance(value, bool):
        raise CaptureRejected(f"{key} must be true or false", key)
    return value


def _env(payload: dict) -> dict[str, str]:
    raw = payload.get("env")
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise CaptureRejected("env must be an object of strings", "env")
    if len(raw) > ENV_MAX_KEYS:
        raise CaptureRejected(f"env exceeds {ENV_MAX_KEYS} keys", "env")
    out = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not _ENV_KEY_RE.match(key or ""):
            raise CaptureRejected(f"env name {key!r} is not an identifier",
                                  "env")
        if len(key) > _ENV_KEY_MAX:
            raise CaptureRejected(f"env name {key!r} is too long", "env")
        if not isinstance(value, str):
            raise CaptureRejected(f"env {key} must be a string", "env")
        out[key] = value[:_ENV_VALUE_MAX]
    return out


def _worktree(payload: dict) -> Optional[dict]:
    """The requested worktree, or None for a run in `cwd` as it stands.

    Keyed off the branch exactly as the launch route is: a worktree with no
    branch named is not a request anything can serve, so the branch *is* the
    ask. Over-long branches are refused rather than clipped — the string is
    created, not looked up, so a shortened one would put the run on a branch
    nobody named.
    """
    spec = payload.get("worktree")
    if spec is None:
        return None
    if not isinstance(spec, dict):
        raise CaptureRejected("worktree must be an object with a branch",
                              "worktree")
    branch = _string(spec, "branch", _ID_MAX)
    if not branch:
        return None
    return {"branch": branch, "base": _string(spec, "base", _ID_MAX)}


def _skill(payload: dict) -> Optional[dict]:
    """The skill the run opens with, or None for a bare prompt.

    The name is the invocation: the launch prompt begins `/<name> <args>` and
    the CLI resolves it exactly as a typed slash command, so a leading slash is
    tolerated and stripped rather than doubled.
    """
    spec = payload.get("skill")
    if spec is None:
        return None
    if not isinstance(spec, dict):
        raise CaptureRejected("skill must be an object with a name", "skill")
    name = _skill_name(spec.get("name"))
    if not name:
        return None
    skill = {"name": name, "args": _string(spec, "args", _SKILL_ARGS_MAX)}
    also = _also(spec.get("also"), name)
    if also:
        skill["also"] = also
    return skill


def _skill_name(value) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise CaptureRejected("skill name must be a string", "skill")
    name = value.strip().lstrip("/")
    if len(name) > _ID_MAX:
        raise CaptureRejected(f"skill name exceeds {_ID_MAX} characters",
                              "skill")
    if name and not _SKILL_NAME_RE.match(name):
        raise CaptureRejected(f"skill name {name!r} is not a slash command",
                              "skill")
    return name


def _also(value, first: str) -> list[str]:
    """The further skills the run is told to invoke, de-duplicated in order.

    Only the first skill can be the prompt's slash command — the CLI expands
    one, at the start — so these are named in the prompt for the agent to load
    with its Skill tool.
    """
    if value is None:
        return []
    if not isinstance(value, list):
        raise CaptureRejected("skill.also must be a list of names", "skill")
    names = []
    for item in value:
        name = _skill_name(item)
        if name and name != first and name not in names:
            names.append(name)
    if len(names) > SKILL_ALSO_MAX:
        raise CaptureRejected(
            f"skill.also exceeds {SKILL_ALSO_MAX} skills", "skill")
    return names


def _launch_choice(payload: dict, provider: str, caps: dict) -> str:
    """The `launch` value, held to the modes the provider declares.

    An explicit mode the provider does not declare is a rejection naming it,
    not a silent drop; an absent one stays the provider's structured channel.
    """
    value = _choice(payload, "launch", LAUNCH_MODES)
    if value and value not in caps["launch_modes"]:
        raise CaptureRejected(
            f"{provider} does not declare launch mode {value!r}", "launch")
    return value or LAUNCH_SDK


def _model(payload: dict, provider: str, caps: dict) -> str:
    value = _string(payload, "model", _ID_MAX)
    if value and "model" not in caps["fields"]:
        raise CaptureRejected(f"{provider} does not accept a model", "model")
    return value


def _vocabulary_field(payload: dict, key: str, allowed, caps: dict) -> str:
    """A field in the CLI's own enum vocabulary, or "" when the provider does
    not declare it.

    These mirror `claude_agent_sdk`'s enums, so a provider without them drops
    the values instead of dying on words that have no use there (feature #140).
    """
    if key not in caps["fields"]:
        return ""
    return _choice(payload, key, allowed)


def _params(payload: dict, provider: str, caps: dict) -> dict:
    """The launch parameters in the launcher's own vocabulary."""
    return {
        "cwd": _string(payload, "cwd", _PATH_MAX),
        "model": _model(payload, provider, caps),
        "permission_mode": _vocabulary_field(
            payload, "permission_mode", client.PERMISSION_MODES, caps),
        "effort": _vocabulary_field(
            payload, "effort", client.EFFORT_LEVELS, caps),
        "one_shot": _flag(payload, "one_shot"),
        "env": _env(payload),
        "worktree": _worktree(payload),
        "skill": _skill(payload),
        "launch": _launch_choice(payload, provider, caps),
        "provider": provider,
    }


def _launch_capabilities(provider: str) -> dict:
    """The provider's launch declaration, or a rejection when it has none: a
    provider without a launch channel must not arm and then run as Claude."""
    caps = registry.build_provider(provider).launch_capabilities()
    if not caps["launch_modes"]:
        raise CaptureRejected(
            f"{provider} has no task launch channel", "provider")
    return caps


def parse_arm(payload) -> dict:
    """`{params, verify_cmd, autorun}` for `store.arm_task`, or
    `CaptureRejected` naming the offending field.

    An empty body is valid: it arms the task with the install's own defaults.
    """
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise CaptureRejected("body must be a JSON object")
    unknown = sorted(set(payload) - ARM_FIELDS)
    if unknown:
        raise CaptureRejected(f"unknown field: {unknown[0]}", unknown[0])
    provider = _choice(payload, "provider",
                       registry.list_provider_ids()) or "claude"
    return {
        "params": _params(payload, provider, _launch_capabilities(provider)),
        "verify_cmd": _string(payload, "verify_cmd", _CMD_MAX) or None,
        "autorun": int(_flag(payload, "autorun")),
    }
