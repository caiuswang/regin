"""Agent → human message channel (the `send_to_user` inbox).

A durable record of every message an agent pushed at the user mid-task,
written by the PostToolUse hook when an `mcp__*__send_to_user` call lands
(see `hook_manager/handlers/post_tool_trace`). Unlike the demo it
replaces, this is a *canonical* store — not reconstructed from
`session_spans` at read time — so a dropped span or a query typo can't
make a message vanish.

Each row keeps a back-link to the originating tool span (`span_id`) so
the inbox can still deep-link into the trace, and to the emitting
subagent (`agent_id`) so multi-agent runs attribute correctly.

`msg_key` powers *supersede-in-place*: a long task can push
`key="build"` repeatedly ("compiling… 40%" → "done") and the inbox shows
one card that updates, not six stacked progress lines. Without a key,
every call is a distinct row.

Read/ack/dismiss timestamps drive the cross-session inbox's unread badge.
The table is mutable (these columns change after insert), which is why it
is NOT modelled on the append-only `session_spans` convention.
"""

from __future__ import annotations

from typing import Optional

from sqlmodel import Column, Field, Integer, String, Text
from sqlalchemy import text

from lib.orm.base import Base


# Message types, ordered by severity (low → high). The webhook gate and
# the inbox styling both key off this ordering. `lesson` doubles as the
# explicit capture endpoint into the agent-memory store: the hook that
# persists the message also calls `lib.memory.remember` for it (see
# `hook_manager/handlers/post_tool_trace._record_agent_message`).
MESSAGE_TYPES: tuple[str, ...] = (
    "progress", "note", "lesson", "result", "summary", "warning", "blocker",
)
_SEVERITY_RANK: dict[str, int] = {t: i for i, t in enumerate(MESSAGE_TYPES)}
DEFAULT_MESSAGE_TYPE = "progress"


def severity_rank(msg_type: str | None) -> int:
    """Severity ordinal for a message type (unknown → progress's rank)."""
    return _SEVERITY_RANK.get(msg_type or "", _SEVERITY_RANK[DEFAULT_MESSAGE_TYPE])


class AgentMessage(Base, table=True):
    """One message an agent sent to the user via `send_to_user`."""

    __tablename__ = "agent_messages"

    id: Optional[int] = Field(default=None, primary_key=True)
    # Session (Claude Code session id == trace_id elsewhere in regin).
    trace_id: str = Field(
        sa_column=Column("trace_id", String, nullable=False, index=True))
    # Originating tool span + emitting subagent, for deep-linking and
    # multi-agent attribution. Nullable: a message may outlive its span
    # row, and main-agent messages carry no agent_id.
    span_id: Optional[str] = Field(default=None,
                                   sa_column=Column("span_id", String))
    agent_id: Optional[str] = Field(default=None,
                                    sa_column=Column("agent_id", String))
    agent_type: Optional[str] = Field(default=None,
                                      sa_column=Column("agent_type", String))

    msg_type: str = Field(
        sa_column=Column("msg_type", String, nullable=False,
                         server_default=text("'progress'")))
    title: Optional[str] = Field(default=None, sa_column=Column("title", Text))
    body: str = Field(
        sa_column=Column("body", Text, nullable=False,
                         server_default=text("''")))
    # Supersede key (scoped to the session). NULL → always a new row.
    msg_key: Optional[str] = Field(default=None,
                                   sa_column=Column("msg_key", String))
    # JSON array of {label, href} link objects (file paths / URLs / span ids).
    links: Optional[str] = Field(default=None, sa_column=Column("links", Text))

    pinned: int = Field(
        sa_column=Column("pinned", Integer, nullable=False,
                         server_default=text("0")))
    # Bumped each time a keyed message is superseded in place.
    version: int = Field(
        sa_column=Column("version", Integer, nullable=False,
                         server_default=text("1")))
    # Webhook dispatch outcome: 'sent' | 'failed' | 'skipped' | NULL (never
    # attempted). Display-only — surfaced in the inbox for blocker triage.
    webhook_status: Optional[str] = Field(
        default=None, sa_column=Column("webhook_status", String))

    # Read-state timestamps (ISO-8601). NULL == unread / un-acked / live.
    read_at: Optional[str] = Field(default=None,
                                   sa_column=Column("read_at", Text))
    acked_at: Optional[str] = Field(default=None,
                                    sa_column=Column("acked_at", Text))
    dismissed_at: Optional[str] = Field(default=None,
                                        sa_column=Column("dismissed_at", Text))

    is_test: int = Field(
        sa_column=Column("is_test", Integer, nullable=False,
                         server_default=text("0")))

    created_at: str = Field(
        sa_column=Column("created_at", Text, nullable=False,
                         server_default=text("(datetime('now'))")))
    updated_at: str = Field(
        sa_column=Column("updated_at", Text, nullable=False,
                         server_default=text("(datetime('now'))")))


__all__ = [
    "AgentMessage",
    "MESSAGE_TYPES",
    "DEFAULT_MESSAGE_TYPE",
    "severity_rank",
]
