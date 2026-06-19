"""Scoping wrapper — a policy layer the engine never sees.

The store filters on opaque scope strings (`global`, `repo:<name>`); this
module decides what those strings are for a given capture or recall,
based on `settings.agent_memory.scope_policy`:

  * `global`          — everything written and recalled in one scope.
  * `per-repo`        — writes from inside a registered repo are stamped
                        `repo:<name>`; recall narrows to that repo + global.
  * `per-repo-tagged` — repo-local writes, globally visible recall
                        (scope recorded for provenance, never filtered on).
"""

from __future__ import annotations

import os
from typing import Optional

from lib.settings import settings


def _repo_name_for(cwd: str) -> Optional[str]:
    """Name of the registered repo containing `cwd`, or None."""
    try:
        resolved = os.path.realpath(cwd)
    except OSError:
        return None
    for repo_path in settings.repo_paths:
        root = os.path.realpath(str(repo_path))
        if resolved == root or resolved.startswith(root + os.sep):
            return os.path.basename(root)
    return None


def resolve_write_scope(cwd: Optional[str]) -> str:
    """Scope string stamped onto a new memory captured from `cwd`."""
    if settings.agent_memory.scope_policy == "global" or not cwd:
        return "global"
    name = _repo_name_for(cwd)
    return f"repo:{name}" if name else "global"


def resolve_recall_scope(cwd: Optional[str]) -> Optional[str]:
    """Scope filter for recall from `cwd`; None means no narrowing."""
    if settings.agent_memory.scope_policy != "per-repo" or not cwd:
        return None
    name = _repo_name_for(cwd)
    return f"repo:{name}" if name else None


__all__ = ["resolve_write_scope", "resolve_recall_scope"]
