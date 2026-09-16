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

# The escape hatch a caller passes to opt out of the scope policy entirely.
# Not a stored scope: no memory is ever written with it.
ALL_SCOPES = "all"


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


def resolve_tool_scope(requested: Optional[str],
                       cwd: Optional[str]) -> Optional[str]:
    """Scope filter for a *deliberate* retrieval — the `recall` / `index_*`
    front-ends the skills call, as opposed to the hook's auto-injection.

    Both front-ends take a caller-supplied scope string, so the policy has to
    yield to an explicit request while still applying to the common case where
    the caller passes nothing:

      * `"all"`   — every scope, the pre-policy behaviour, kept as the
                    explicit way to ask across repos.
      * anything else truthy — used verbatim (`"repo:regin"`, `"global"`).
      * empty/None — `resolve_recall_scope(cwd)`, so `per-repo` reaches the
                     retrieval a skill performs and not just the injected
                     `<recalled_experience>` block.
    """
    if requested == ALL_SCOPES:
        return None
    if requested:
        return requested
    return resolve_recall_scope(cwd)


__all__ = ["ALL_SCOPES", "resolve_write_scope", "resolve_recall_scope",
           "resolve_tool_scope"]
