"""`regin memory` — CLI surface over the cross-session agent memory.

Thin wrappers around the `lib.memory` facade: recall/list/stats for
inspection, reflect for consolidation, distill for proposing memories
from a finished session, approve/forget for curation. The engine itself
is documented under *Agent Memory* in `ARCHITECTURE.md`.
"""

from __future__ import annotations

# Import order is `regin memory --help` order: each module registers its
# commands on `memory_app` as it is imported.
from ._common import memory_app
from . import recall, authoring, linking, curation  # noqa: F401
from .linking import _apply_assignments, _fmt_link, _select_link_rows
