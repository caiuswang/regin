"""`regin topics ...` - maintain repo-local topic graphs."""

from __future__ import annotations

# Import order is `regin topics --help` order: each module registers its
# commands on `topics_app` as it is imported.
from ._common import topics_app
from . import graph, proposals, sync, wiki, audit  # noqa: F401
from .audit import (  # noqa: F401
    cmd_topics_audit, cmd_topics_audit_fix, cmd_topics_group,
    cmd_topics_split_leaf,
)
from .graph import (  # noqa: F401
    cmd_topics_bootstrap, cmd_topics_cascade_stale, cmd_topics_delete,
    cmd_topics_digest_refs, cmd_topics_drift, cmd_topics_drift_dismiss,
    cmd_topics_drift_note, cmd_topics_evolve, cmd_topics_list,
    cmd_topics_promote, cmd_topics_proposal_finish, cmd_topics_proposal_reap,
    cmd_topics_propose, cmd_topics_review_finish, cmd_topics_review_note,
    cmd_topics_scan,
)
from .proposals import (  # noqa: F401
    cmd_topics_proposal_apply, cmd_topics_proposal_diff,
    cmd_topics_proposal_export, cmd_topics_proposal_feedback,
    cmd_topics_proposal_import, cmd_topics_proposal_list,
    cmd_topics_proposal_review_state, cmd_topics_proposal_show,
    cmd_topics_proposal_wiki,
)
from .sync import (  # noqa: F401
    cmd_topics_check, cmd_topics_import, cmd_topics_install_hook,
    cmd_topics_rebuild_query_df, cmd_topics_reconcile,
    cmd_topics_router_skill,
)
from .wiki import (  # noqa: F401
    _render_topic_wiki, cmd_topics_backfill_tiers,
    cmd_topics_backfill_topic_wiki, cmd_topics_route, cmd_topics_wiki,
    cmd_topics_wiki_debt, cmd_topics_wiki_stats,
)
