"""Trace dashboard + ingest service layer.

Wraps the SQL that backs the Trace views (/api/skill-reads,
/api/mcp-calls, /api/sessions/<trace_id>/materialize) and the span
ingest path behind a function interface. The SQL itself stays raw —
SQLite-specific `json_extract` + CTEs with `ROW_NUMBER()` + the
~60-line `ON CONFLICT DO UPDATE` in the spans upsert don't translate
cleanly to SQLAlchemy expression language — but blueprints no longer
need to know about `get_connection` to call it.

Implementation is split across ``queries`` (read-side) and ``ingest``
(write-side); this package re-exports the public surface so
``from lib.trace.trace_service import X`` stays stable.
"""

from __future__ import annotations

from lib.trace.trace_service.ingest import (
    _SESSIONS_UPSERT_SQL,
    _span_counter_buckets,
    ingest_session_spans,
    ingest_session_status,
    ingest_tool_attribution,
    ingest_turn_usage,
    materialize_session,
)
from lib.trace.trace_service.queries import (
    fetch_session_paginated,
    fetch_session_projection,
    fetch_tool_token_rollup,
    fetch_turn_usage,
    list_mcp_calls_page,
    list_skill_reads_page,
)


__all__ = [
    "_SESSIONS_UPSERT_SQL",
    "_span_counter_buckets",
    "fetch_session_paginated",
    "fetch_session_projection",
    "fetch_tool_token_rollup",
    "fetch_turn_usage",
    "ingest_session_spans",
    "ingest_session_status",
    "ingest_tool_attribution",
    "ingest_turn_usage",
    "list_mcp_calls_page",
    "list_skill_reads_page",
    "materialize_session",
]
