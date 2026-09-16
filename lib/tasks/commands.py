"""The project's command table (`<repo>/.regin/commands.json`).

Three commands — how this project runs its tests, its migrations, and its
end-to-end suite — named once, in the project, in git. Every generated
slice prompt reads them from here rather than guessing, so "the test
command" has one answer per project instead of one per author.

The file's shape carries the whole contract: all three keys must appear.
A value may be `null`, meaning "this project has none" — but only with a
matching entry in `reasons`, because a missing command and a forgotten
one must stay distinguishable. A key that is simply absent is the
forgotten case, and it is refused by name.

When the file does not exist, detection produces a *draft* and prints it.
It is never written: a command table is a claim about how a project is
built, and a machine that guessed one is not entitled to make that claim
on the author's behalf.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Optional, TextIO

from lib.activity_log import get_activity_logger

log = get_activity_logger("tasks")

COMMANDS_RELPATH = os.path.join(".regin", "commands.json")

COMMAND_KEYS = ("test", "migrate", "e2e")

UNDETECTED_REASON = "探测不出"

DEFAULT_SKILL = "goal-verified-treenav"

# What `task commands` exits with when it printed a draft rather than a
# contract. Distinct from 0 (loaded) and 1 (refused) because both of those
# also write JSON to stdout: a caller reading only stdout cannot otherwise
# tell an agreed command table from a machine's guess.
DRAFT_EXIT = 3


class CommandsError(ValueError):
    """The command table cannot be used as written."""

    def __init__(self, message: str, key: Optional[str] = None) -> None:
        super().__init__(message)
        self.key = key


class CommandsRejected(CommandsError):
    """A present file that violates the contract, naming what broke it."""


class CommandsAbsent(CommandsError):
    """No file at all — carries the detected draft for the caller to print."""

    def __init__(self, path: str, draft: "ProjectCommands") -> None:
        super().__init__(f"no command table at {path}; a draft follows")
        self.path = path
        self.draft = draft


@dataclass(frozen=True)
class ProjectCommands:
    test: Optional[str] = None
    migrate: Optional[str] = None
    e2e: Optional[str] = None
    reasons: dict[str, str] = field(default_factory=dict)

    def get(self, key: str) -> Optional[str]:
        if key not in COMMAND_KEYS:
            raise KeyError(key)
        return getattr(self, key)

    def to_json_obj(self) -> dict:
        obj: dict = {key: self.get(key) for key in COMMAND_KEYS}
        obj["reasons"] = {k: self.reasons[k] for k in COMMAND_KEYS if k in self.reasons}
        return obj


def commands_path(repo_root: str) -> str:
    return os.path.join(repo_root, COMMANDS_RELPATH)


def _refuse_missing_keys(raw: dict) -> None:
    missing = [key for key in COMMAND_KEYS if key not in raw]
    if missing:
        raise CommandsRejected(
            "the command table is missing "
            + ", ".join(repr(k) for k in missing)
            + " — a command with no value must still appear, as null with a reason",
            missing[0],
        )


def _reasons_of(raw: dict) -> dict[str, str]:
    reasons = raw.get("reasons") or {}
    if not isinstance(reasons, dict):
        raise CommandsRejected("'reasons' must be a JSON object", "reasons")
    return reasons


def _value_of(key: str, value: object, reasons: dict) -> Optional[str]:
    if value is None:
        reason = reasons.get(key)
        if not (isinstance(reason, str) and reason.strip()):
            raise CommandsRejected(
                f"{key!r} is null and needs a reason — add reasons.{key} "
                "saying why this project has no such command",
                key,
            )
        return None
    if not isinstance(value, str) or not value.strip():
        raise CommandsRejected(f"{key!r} must be a non-empty string or null", key)
    return value.strip()


def parse_project_commands(raw: object) -> ProjectCommands:
    """Validate a decoded command table, refusing by name."""
    if not isinstance(raw, dict):
        raise CommandsRejected("the command table must be a JSON object")
    _refuse_missing_keys(raw)
    reasons = _reasons_of(raw)
    values = {key: _value_of(key, raw[key], reasons) for key in COMMAND_KEYS}
    kept = {k: v for k, v in reasons.items() if k in COMMAND_KEYS and isinstance(v, str)}
    return ProjectCommands(reasons=kept, **values)


def load_project_commands(repo_root: str) -> ProjectCommands:
    """Read and validate `<repo_root>/.regin/commands.json`.

    Raises `CommandsAbsent` (carrying a printable draft) when the file is
    not there, and `CommandsRejected` when it is there but breaks the
    contract.
    """
    path = commands_path(repo_root)
    try:
        with open(path) as handle:
            raw = json.load(handle)
    except FileNotFoundError:
        raise CommandsAbsent(path, draft_project_commands(repo_root)) from None
    except json.JSONDecodeError as exc:
        raise CommandsRejected(f"{path} is not valid JSON: {exc}") from None
    commands = parse_project_commands(raw)
    log.read("project_commands_loaded", path=path)
    return commands


# ── detection ────────────────────────────────────────────────

def _venv_python(repo_root: str) -> Optional[str]:
    return ".venv/bin/python" if os.path.exists(
        os.path.join(repo_root, ".venv", "bin", "python")) else None


def _detect_test(repo_root: str) -> Optional[str]:
    if os.path.exists(os.path.join(repo_root, "pyproject.toml")) or os.path.isdir(
            os.path.join(repo_root, "tests")):
        python = _venv_python(repo_root) or "python"
        return f"{python} -m pytest -q"
    if os.path.exists(os.path.join(repo_root, "package.json")):
        return "npm test"
    return None


def _detect_migrate(repo_root: str) -> Optional[str]:
    if not os.path.exists(os.path.join(repo_root, "alembic.ini")):
        return None
    python = _venv_python(repo_root)
    return f"{python} -m alembic upgrade head" if python else "alembic upgrade head"


_E2E_CONFIGS = ("playwright.config.js", "playwright.config.ts")


def _detect_e2e(repo_root: str) -> Optional[str]:
    for directory in (".", "frontend", "e2e"):
        for name in _E2E_CONFIGS:
            if os.path.exists(os.path.join(repo_root, directory, name)):
                prefix = "" if directory == "." else f"cd {directory} && "
                return f"{prefix}npx playwright test"
    return None


_DETECTORS = {"test": _detect_test, "migrate": _detect_migrate, "e2e": _detect_e2e}


def draft_project_commands(repo_root: str) -> ProjectCommands:
    """Probe the project for its three commands; undetected ⇒ null + reason."""
    values: dict[str, Optional[str]] = {}
    reasons: dict[str, str] = {}
    for key in COMMAND_KEYS:
        detected = _DETECTORS[key](repo_root)
        values[key] = detected
        if detected is None:
            reasons[key] = UNDETECTED_REASON
    return ProjectCommands(reasons=reasons, **values)


def render_draft(commands: ProjectCommands) -> str:
    return json.dumps(commands.to_json_obj(), indent=2, ensure_ascii=False)


def print_draft(commands: ProjectCommands, path: str, out: TextIO) -> None:
    """Write a draft to `out` and nothing to disk — see the module docstring."""
    out.write(render_draft(commands) + "\n")
    out.write(
        f"\n# draft only — nothing was written. Review it, then save it as {path}.\n")
