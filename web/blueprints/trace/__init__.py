"""Trace endpoints: span ingest + session/skill-read/MCP-call dashboards.

Four categories of endpoint live in this blueprint, each in its own
submodule that registers routes onto `trace_bp`:

1. **Ingest** — `POST /api/session-spans`, `POST /api/skill-reads`. Both
   are driven by the hook-plugin retry loop, so they enforce strict
   validation + dedup and roll back cleanly on any DB error mid-batch.

2. **Dashboards** — `GET /api/skill-reads`, `GET /api/mcp-calls`,
   `GET /api/sessions`. Each grinds the last few hundred session_spans
   or skill_reads rows into the shape the Trace view consumes; the
   single-query CTE approach (plan_latest + test_markers) replaces what
   used to be per-row correlated subqueries.

3. **Projection** — `GET /api/sessions/<trace_id>` is read-only and
   projects spans into an in-memory tree via `lib.trace.projection`.
   `POST /api/sessions/<trace_id>/materialize` wraps the same projection
   in BEGIN IMMEDIATE and persists the widened envelopes/grafted parents
   back to disk.

4. **Observability** — `GET /api/ingest-errors` tails
   `~/.claude/traces/ingest-errors.jsonl` with counts by endpoint /
   error_type / gave_up, so operators can spot drop patterns without
   SSHing to each box.

All validators and ingest caps come from `web.helpers`; all pure span-tree
transforms come from `lib.trace.projection`.
"""

from __future__ import annotations

from flask import Blueprint


trace_bp = Blueprint('trace', __name__)


# Submodule imports register routes onto trace_bp via side effects.
# Keep these AFTER trace_bp is defined so the submodules can import it.
from web.blueprints.trace import (  # noqa: E402,F401
    agent_messages,
    ingest_errors,
    mcp_calls,
    prompt_images,
    sessions,
    skill_reads,
    spans_ingest,
    turn_usage,
)
