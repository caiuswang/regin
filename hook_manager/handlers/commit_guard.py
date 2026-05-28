"""Handler: run a per-repo pre-commit check when on a watched branch.

This handler is *library code* — it implements the behavior but doesn't
know which repos/branches/scripts to guard. The concrete configuration
lives in `custom_registry.py` because guard lists are inherently
user-/project-specific.

Typical wiring (in `custom_registry.py`):

    from .core import Handler
    from .handlers.commit_guard import GuardedRepo, make_handler

    _GUARDS = [
        GuardedRepo(repo_dir='/path/to/repo',
                    branch='feature/PT-1234',
                    check_script='/path/to/repo/.grit/check-staged.sh'),
    ]

    CUSTOM_HANDLERS = [
        Handler(
            name='commit_guard',
            events=['PreToolUse'],
            kind='gate',
            priority=20,
            fn=make_handler(_GUARDS),
        ),
    ]
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from typing import Callable, Sequence

from ..core import HookPayload, HookResponse

_GIT_COMMIT_RE = re.compile(r'(?:^|[\s;&|])git\s+commit(?:\s|$)')


@dataclass(frozen=True)
class GuardedRepo:
    repo_dir: str
    branch: str
    check_script: str


def _current_branch(repo_dir: str) -> str | None:
    try:
        out = subprocess.run(
            ['git', '-C', repo_dir, 'branch', '--show-current'],
            capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() or None
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None


def _run_check(script: str, repo_dir: str) -> tuple[int, str]:
    try:
        out = subprocess.run(
            ['bash', script, repo_dir],
            capture_output=True, text=True, timeout=30,
        )
        return out.returncode, (out.stdout + out.stderr).strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        return 0, f'[commit_guard] skipped {script}: {exc}'


def make_handler(
    guarded_repos: Sequence[GuardedRepo],
) -> Callable[[HookPayload], HookResponse | None]:
    """Return a handler function bound to a specific list of guarded repos.

    Pure factory — no module-level state. Each call returns an independent
    closure so a settings.json could register multiple commit_guard handlers
    with different repo lists (unusual but supported).
    """
    repos = tuple(guarded_repos)

    def handle(payload: HookPayload) -> HookResponse | None:
        if not repos:
            return None
        if payload.tool_name != 'Bash':
            return None
        cmd = (payload.tool_input or {}).get('command', '')
        if not isinstance(cmd, str) or not _GIT_COMMIT_RE.search(cmd):
            return None

        messages: list[str] = []
        blocked = False
        for repo in repos:
            if not os.path.isdir(repo.repo_dir):
                continue
            if _current_branch(repo.repo_dir) != repo.branch:
                continue
            if not os.path.isfile(repo.check_script):
                continue
            rc, out = _run_check(repo.check_script, repo.repo_dir)
            if rc != 0:
                blocked = True
                messages.append(
                    f'[{os.path.basename(repo.repo_dir)}] check-staged failed '
                    f'(exit {rc}):\n{out}'
                )

        if blocked:
            body = '\n\n'.join(messages)
            return HookResponse(
                decision='block',
                decision_reason=body,
                permission_decision='deny',
                permission_reason=body,
            )
        return None

    return handle
