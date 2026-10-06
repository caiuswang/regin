"""Session endpoints on `trace_bp`, one submodule per responsibility.

Importing this package registers every `/api/sessions*` and
`/api/session-tags` route: each submodule decorates its handlers onto the
shared `trace_bp` at import time.
"""

from __future__ import annotations

from web.blueprints.trace.sessions import (  # noqa: F401
    admin,
    listing,
    map_view,
    spans,
    subtrees,
)
from web.blueprints.trace.sessions.common import _alias_group_ids  # noqa: F401
from web.blueprints.trace.sessions.listing import (  # noqa: F401
    _RUN_ORIGINS, _session_active_clause,
)
from web.blueprints.trace.sessions.map_view import (  # noqa: F401
    _bridge_reachability, _is_live_tool_row, _kept_map_attrs,
)
from web.blueprints.trace.sessions.phase import (  # noqa: F401
    _PHASE_PRECEDENCE, _main_phase, _owned_run_phase, _phase_from_roster,
    _phase_rollup, _session_phase, _session_repo_name, _session_summary,
    _waiting_kind,
)
from web.blueprints.trace.sessions.roster import (  # noqa: F401
    _roster_rows, _roster_with_activity,
)
from web.blueprints.trace.sessions.steers import (  # noqa: F401
    _delivered_key, _partition_steers, _pending_bridge_steers,
    _queued_prompts, _session_has_ended,
)
from web.blueprints.trace.sessions.subtrees import _body_needed  # noqa: F401
from web.blueprints.trace.sessions.tasks import (  # noqa: F401
    _fetch_session_task_list,
)
from web.blueprints.trace.sessions.admin import (  # noqa: F401
    _SESSION_DELETE_TARGETS, api_session_batch_close, api_session_batch_delete,
    api_session_close,
    api_session_delete, api_session_features, api_session_materialize,
    api_session_repair_spans, api_session_tag_add, api_session_tag_remove,
    api_session_tags,
)
from web.blueprints.trace.sessions.listing import api_sessions  # noqa: F401
from web.blueprints.trace.sessions.map_view import (  # noqa: F401
    api_background_shell_output, api_session_map,
)
from web.blueprints.trace.sessions.phase import (  # noqa: F401
    IDLE_SETTLE_SEC, INACTIVE_THRESHOLD_SEC, WORKING_WINDOW_SEC,
)
from web.blueprints.trace.sessions.spans import (  # noqa: F401
    api_session_workflow_runs, api_span_ancestors, api_span_content,
    api_span_rewind,
)
from web.blueprints.trace.sessions.subtrees import (  # noqa: F401
    api_session_detail, api_session_span_children, api_session_subtrees,
)
