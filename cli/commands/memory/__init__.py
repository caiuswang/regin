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
from ._common import tree_nav_module  # noqa: F401
from .authoring import (  # noqa: F401
    cmd_backfill_titles, cmd_distill, cmd_exemplar_add, cmd_exemplar_forget,
    cmd_exemplar_list, cmd_exemplar_rm, cmd_export_tree, cmd_import_tree,
    cmd_list, cmd_reflect, cmd_remember, cmd_retitle, cmd_stats,
    cmd_topic_decide, cmd_topic_feedback,
)
from .curation import (  # noqa: F401
    cmd_approve, cmd_curate_apply, cmd_eval, cmd_forget, cmd_restore,
    cmd_supersede,
)
from .linking import (  # noqa: F401
    _apply_assignments, _fmt_link, _select_link_rows, cmd_consolidate_skills,
    cmd_link_topics,
)
from .recall import (  # noqa: F401
    cmd_index_expand, cmd_index_fetch, cmd_index_root, cmd_read, cmd_recall,
    cmd_recall_for_task,
)
