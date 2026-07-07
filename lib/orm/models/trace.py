"""OpenTelemetry-inspired session/span tables + related trace rows.

`session_spans` is the single source of truth; `sessions` holds
incrementally-maintained aggregates (titles, counters, status) to keep
the dashboard list read O(1). `skill_reads` and `plan_sessions` are
narrower logs emitted by specific PostToolUse hooks.

These model shapes match the schema declared in `web/startup.py`
(which still owns the CREATE TABLE calls for now — B.5 will hand the
responsibility to Alembic migrations). Any column added to a table
there must be mirrored as a field here if queries want to read it.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import Float, LargeBinary, text
from sqlmodel import Column, Field, Integer, String, Text

from lib.orm.base import Base


class SessionTraceMap(Base, table=True):
    """Structural skeleton of a session trace — relationships and timeline.

    Separated from `session_spans` so the frontend can load the full
    session shape (all spans, all parent links) without dragging along
    the potentially-large `attributes` JSON blobs. Content is fetched
    on-demand per span via the content endpoint.
    """

    __tablename__ = "session_trace_map"

    id: Optional[int] = Field(default=None, primary_key=True)
    trace_id: str = Field(sa_column=Column("trace_id", String, nullable=False, index=True))
    span_id: str = Field(sa_column=Column("span_id", String, nullable=False))
    parent_id: Optional[str] = Field(
        default=None,
        sa_column=Column("parent_id", String, index=True),
    )
    name: str = Field(sa_column=Column("name", String, nullable=False))
    kind: Optional[str] = Field(
        default=None,
        sa_column=Column("kind", String, server_default=text("'internal'")),
    )
    start_time: str = Field(sa_column=Column("start_time", Text, nullable=False))
    end_time: Optional[str] = Field(default=None, sa_column=Column("end_time", Text))
    duration_ms: Optional[int] = Field(default=None,
                                       sa_column=Column("duration_ms", Integer))
    status_code: Optional[str] = Field(
        default=None,
        sa_column=Column("status_code", String, server_default=text("'UNSET'")),
    )
    status_message: Optional[str] = Field(default=None,
                                          sa_column=Column("status_message", Text))
    created_at: Optional[str] = Field(
        default=None,
        sa_column=Column("created_at", Text, nullable=False,
                         server_default=text("(datetime('now'))")),
    )


class SessionSpan(Base, table=True):
    __tablename__ = "session_spans"

    id: Optional[int] = Field(default=None, primary_key=True)
    trace_id: str = Field(sa_column=Column("trace_id", String, nullable=False))
    span_id: str = Field(sa_column=Column("span_id", String, nullable=False))
    parent_id: Optional[str] = Field(default=None,
                                     sa_column=Column("parent_id", String))
    name: str = Field(sa_column=Column("name", String, nullable=False))
    kind: Optional[str] = Field(
        default=None,
        sa_column=Column("kind", String, server_default=text("'internal'")),
    )
    start_time: str = Field(sa_column=Column("start_time", Text, nullable=False))
    end_time: Optional[str] = Field(default=None, sa_column=Column("end_time", Text))
    duration_ms: Optional[int] = Field(default=None,
                                       sa_column=Column("duration_ms", Integer))
    attributes: str = Field(
        sa_column=Column("attributes", Text, nullable=False,
                         server_default=text("'{}'")),
    )
    status_code: Optional[str] = Field(
        default=None,
        sa_column=Column("status_code", String, server_default=text("'UNSET'")),
    )
    status_message: Optional[str] = Field(default=None,
                                          sa_column=Column("status_message", Text))
    # Per-tool token attribution (populated for `tool.*` spans only).
    # Anthropic returns one usage block per assistant turn, never per
    # tool — these columns are tokenized estimates of how much each
    # tool_use block and its tool_result contributed.
    output_tokens: Optional[int] = Field(
        default=None,
        sa_column=Column("output_tokens", Integer),
    )
    input_tokens: Optional[int] = Field(
        default=None,
        sa_column=Column("input_tokens", Integer),
    )
    image_tokens: Optional[int] = Field(
        default=None,
        sa_column=Column("image_tokens", Integer),
    )
    cost_usd: Optional[float] = Field(
        default=None,
        sa_column=Column("cost_usd", Float),
    )
    tool_use_id: Optional[str] = Field(
        default=None,
        sa_column=Column("tool_use_id", String),
    )
    turn_uuid: Optional[str] = Field(
        default=None,
        sa_column=Column("turn_uuid", String),
    )
    # Owning agent: NULL = main agent, else the subagent id. Promoted from
    # attributes.agent_id so roster/phase reads group on an indexed column
    # instead of json_extract-scanning. Mirrors db/schema.sql + web/startup.py.
    agent_id: Optional[str] = Field(
        default=None,
        sa_column=Column("agent_id", String),
    )
    # Issuing prompt submission: the hook envelope's `prompt_id` (Claude Code
    # 2.1.195+), stamped by post_tool_trace onto `attributes.source_prompt_id`
    # and promoted here at insert time so the serve-time ladder can value-join
    # a tool span to its `prompt-<uuid>` anchor without json_extract-scanning.
    # The value stays in attributes too; readers fall back to it for rows
    # inserted before this promotion. Mirrors db/schema.sql + web/startup.py.
    source_prompt_id: Optional[str] = Field(
        default=None,
        sa_column=Column("source_prompt_id", String),
    )
    # Capture source: 'hook' (live hook events) or 'transcript' (the
    # transcript scan). The append-only store keeps both; lib/trace/merge.py
    # selects winners at read time. Mirrors db/schema.sql + web/startup.py.
    source: str = Field(
        default="hook",
        sa_column=Column("source", Text, nullable=False,
                         server_default=text("'hook'")),
    )
    created_at: Optional[str] = Field(
        default=None,
        sa_column=Column("created_at", Text, nullable=False,
                         server_default=text("(datetime('now'))")),
    )


class Session(Base, table=True):
    """Per-session aggregates maintained at ingest time."""

    __tablename__ = "sessions"

    trace_id: str = Field(sa_column=Column("trace_id", String, primary_key=True))
    title: Optional[str] = Field(default=None, sa_column=Column("title", Text))
    title_source: Optional[str] = Field(default=None,
                                        sa_column=Column("title_source", String))
    status: Optional[str] = Field(default=None, sa_column=Column("status", String))
    last_start_at: Optional[str] = Field(default=None,
                                         sa_column=Column("last_start_at", Text))
    ended_at: Optional[str] = Field(default=None, sa_column=Column("ended_at", Text))
    ended_reason: Optional[str] = Field(default=None,
                                        sa_column=Column("ended_reason", Text))
    started_at: str = Field(sa_column=Column("started_at", Text, nullable=False))
    last_seen: str = Field(sa_column=Column("last_seen", Text, nullable=False))
    span_count: int = Field(
        sa_column=Column("span_count", Integer, nullable=False,
                         server_default=text("0")),
    )
    skill_reads: int = Field(
        sa_column=Column("skill_reads", Integer, nullable=False,
                         server_default=text("0")),
    )
    file_edits: int = Field(
        sa_column=Column("file_edits", Integer, nullable=False,
                         server_default=text("0")),
    )
    rule_checks: int = Field(
        sa_column=Column("rule_checks", Integer, nullable=False,
                         server_default=text("0")),
    )
    plan_enters: int = Field(
        sa_column=Column("plan_enters", Integer, nullable=False,
                         server_default=text("0")),
    )
    prompts: int = Field(
        sa_column=Column("prompts", Integer, nullable=False,
                         server_default=text("0")),
    )
    tool_calls: int = Field(
        sa_column=Column("tool_calls", Integer, nullable=False,
                         server_default=text("0")),
    )
    is_test: int = Field(
        sa_column=Column("is_test", Integer, nullable=False,
                         server_default=text("0")),
    )
    test_name: Optional[str] = Field(default=None,
                                     sa_column=Column("test_name", Text))
    agent_type: Optional[str] = Field(default=None,
                                      sa_column=Column("agent_type", Text))
    origin: Optional[str] = Field(default="session",
                                  sa_column=Column("origin", String,
                                                   server_default=text("'session'")))
    model: Optional[str] = Field(default=None, sa_column=Column("model", Text))
    # Starting working directory, captured from the `session.start` span's
    # `cwd` attribute (earliest start wins). Display-only; repo membership
    # for filtering lives in the `session_repos` join table.
    cwd: Optional[str] = Field(default=None, sa_column=Column("cwd", Text))
    # Token usage aggregates — populated from the transcript by
    # hook_manager.handlers.turn_trace.
    input_tokens: Optional[int] = Field(default=None,
                                        sa_column=Column("input_tokens", Integer))
    output_tokens: Optional[int] = Field(default=None,
                                         sa_column=Column("output_tokens", Integer))
    cache_read_tokens: Optional[int] = Field(default=None,
                                             sa_column=Column("cache_read_tokens", Integer))
    cache_creation_tokens: Optional[int] = Field(default=None,
                                                 sa_column=Column("cache_creation_tokens", Integer))
    peak_context_tokens: Optional[int] = Field(default=None,
                                               sa_column=Column("peak_context_tokens", Integer))
    # Same as peak_context_tokens but excludes turns whose API call rolled
    # in a server-side sub-call (advisor today; future sub-agents). The
    # parent turn's `usage` block bundles the sub-call's tokens, so the
    # raw peak overstates the main conversation's context size. The
    # SessionsView headline ctx % uses this; peak_context_tokens stays
    # as the all-inclusive number shown alongside when they diverge.
    peak_main_context_tokens: Optional[int] = Field(
        default=None,
        sa_column=Column("peak_main_context_tokens", Integer),
    )
    # Main-flow context peak since the most recent `/compact`. A
    # compaction (manual or auto) resets the live context window, so the
    # all-time peaks above stay pinned at the pre-compaction high. This
    # tracks the live segment and drives the headline ctx% so it drops
    # after the session compacts; equals peak_main when no compaction.
    live_context_tokens: Optional[int] = Field(
        default=None,
        sa_column=Column("live_context_tokens", Integer),
    )
    context_window_tokens: Optional[int] = Field(default=None,
                                                 sa_column=Column("context_window_tokens", Integer))
    # Aggregate USD cost across the session, summed from per-turn
    # `turn_usage.cost_usd`. NULL when the session's model isn't in
    # the pricing catalogue or no turns have been ingested with cost
    # data yet.
    cost_usd: Optional[float] = Field(default=None,
                                      sa_column=Column("cost_usd", Float))
    # Union of root-span intervals (overlaps merged) — agent work time
    # excluding the user-idle gaps between turns. Maintained at ingest
    # time; nullable so legacy rows read NULL until they're touched.
    active_work_ms: Optional[int] = Field(default=None,
                                          sa_column=Column("active_work_ms", Integer))
    created_at: Optional[str] = Field(
        default=None,
        sa_column=Column("created_at", Text, nullable=False,
                         server_default=text("(datetime('now'))")),
    )


class TurnUsage(Base, table=True):
    """Per-assistant-turn token usage — one row per API response.

    Kept separate from `session_spans` because turns aren't really
    operations in the timeline sense (no duration, no causal parent);
    they're metadata about how much context each API call consumed.
    Dedup key is (trace_id, turn_uuid) — the uuid comes from Claude
    Code's transcript JSONL.
    """

    __tablename__ = "turn_usage"

    trace_id: str = Field(sa_column=Column("trace_id", String, primary_key=True))
    turn_uuid: str = Field(sa_column=Column("turn_uuid", String, primary_key=True))
    turn_index: int = Field(sa_column=Column("turn_index", Integer, nullable=False))
    timestamp: str = Field(sa_column=Column("timestamp", Text, nullable=False))
    model: Optional[str] = Field(default=None, sa_column=Column("model", Text))
    input_tokens: int = Field(
        sa_column=Column("input_tokens", Integer, nullable=False,
                         server_default=text("0")),
    )
    output_tokens: int = Field(
        sa_column=Column("output_tokens", Integer, nullable=False,
                         server_default=text("0")),
    )
    cache_read_tokens: int = Field(
        sa_column=Column("cache_read_tokens", Integer, nullable=False,
                         server_default=text("0")),
    )
    cache_creation_tokens: int = Field(
        sa_column=Column("cache_creation_tokens", Integer, nullable=False,
                         server_default=text("0")),
    )
    context_used_tokens: int = Field(
        sa_column=Column("context_used_tokens", Integer, nullable=False,
                         server_default=text("0")),
    )
    # Extended-thinking output is billed/counted separately from
    # output_tokens by Anthropic — keep it on its own bucket.
    reasoning_tokens: Optional[int] = Field(
        default=None,
        sa_column=Column("reasoning_tokens", Integer),
    )
    cost_usd: Optional[float] = Field(
        default=None,
        sa_column=Column("cost_usd", Float),
    )
    # Model reasoning-effort level in force for this turn (e.g. "high",
    # "xhigh"). Sourced from the hook payload's `effort.level`, not the
    # transcript. Per-turn because the user can change it mid-session
    # via the `effort` command. NULL for turns ingested only from a path
    # whose payload carried no effort (e.g. UserPromptSubmit).
    effort_level: Optional[str] = Field(default=None,
                                        sa_column=Column("effort_level", String))
    request_id: Optional[str] = Field(default=None,
                                      sa_column=Column("request_id", Text))
    created_at: Optional[str] = Field(
        default=None,
        sa_column=Column("created_at", Text, nullable=False,
                         server_default=text("(datetime('now'))")),
    )


class SkillRead(Base, table=True):
    __tablename__ = "skill_reads"

    id: Optional[int] = Field(default=None, primary_key=True)
    skill_id: str = Field(sa_column=Column("skill_id", String, nullable=False))
    session_id: Optional[str] = Field(default=None,
                                      sa_column=Column("session_id", String))
    file_path: str = Field(sa_column=Column("file_path", String, nullable=False))
    found: int = Field(
        sa_column=Column("found", Integer, nullable=False,
                         server_default=text("1")),
    )
    source: Optional[str] = Field(
        default=None,
        sa_column=Column("source", String, server_default=text("'read'")),
    )
    command_args: Optional[str] = Field(
        default=None, sa_column=Column("command_args", Text)
    )
    read_at: Optional[str] = Field(
        default=None,
        sa_column=Column("read_at", Text, nullable=False,
                         server_default=text("(datetime('now'))")),
    )


class PlanSession(Base, table=True):
    __tablename__ = "plan_sessions"

    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: str = Field(sa_column=Column("session_id", String, nullable=False))
    plan_filename: str = Field(sa_column=Column("plan_filename", String, nullable=False))
    started_at: str = Field(sa_column=Column("started_at", Text, nullable=False))
    ended_at: Optional[str] = Field(default=None,
                                    sa_column=Column("ended_at", Text))
    draft_completed_at: Optional[str] = Field(
        default=None, sa_column=Column("draft_completed_at", Text))
    review_started_at: Optional[str] = Field(
        default=None, sa_column=Column("review_started_at", Text))


class SessionRepo(Base, table=True):
    """Which registered repos a session touched — the multi-repo join table.

    A session is associated with a repo when one of its high-signal spans
    resolves to that repo by longest-path-prefix against the registered,
    active repo set: the `session.start` cwd (marked `is_primary=1`), any
    `cwd.changed` target, or a file mutation (`tool.Edit`/`tool.Write`/
    `tool.apply_patch`). Reads and Bash are deliberately excluded so an
    incidental read into another repo never tags the session. A session
    with more than one row here is "multi-repo".
    """

    __tablename__ = "session_repos"

    trace_id: str = Field(sa_column=Column("trace_id", String, primary_key=True))
    repo_id: int = Field(sa_column=Column("repo_id", Integer, primary_key=True))
    is_primary: int = Field(
        sa_column=Column("is_primary", Integer, nullable=False,
                         server_default=text("0")),
    )
    created_at: Optional[str] = Field(
        default=None,
        sa_column=Column("created_at", Text, nullable=False,
                         server_default=text("(datetime('now'))")),
    )


class PromptImage(Base, table=True):
    """User-submitted image attached to a `prompt` span.

    Source: `message.content[].type == "image"` parts in the session
    JSONL transcript. `idx` is 1-indexed and matches the `[Image #N]`
    marker in the prompt text. Bytes are stored decoded (BLOB), not
    re-encoded base64.
    """

    __tablename__ = "prompt_images"

    trace_id: str = Field(sa_column=Column("trace_id", String, primary_key=True))
    prompt_span_id: str = Field(
        sa_column=Column("prompt_span_id", String, primary_key=True),
    )
    idx: int = Field(sa_column=Column("idx", Integer, primary_key=True))
    media_type: str = Field(sa_column=Column("media_type", String, nullable=False))
    bytes_: bytes = Field(sa_column=Column("bytes", LargeBinary, nullable=False))
    byte_size: int = Field(sa_column=Column("byte_size", Integer, nullable=False))
    sha256: str = Field(sa_column=Column("sha256", String, nullable=False))
    created_at: Optional[str] = Field(
        default=None,
        sa_column=Column("created_at", Text, nullable=False,
                         server_default=text("(datetime('now'))")),
    )


__all__ = ["SessionSpan", "SessionTraceMap", "Session", "TurnUsage",
           "SkillRead", "PlanSession", "SessionRepo", "PromptImage"]
