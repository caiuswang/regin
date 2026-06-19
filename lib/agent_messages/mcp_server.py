"""`send_to_user` — the agent → human message MCP server.

A stdio MCP server exposing one tool, `send_to_user`, that an agent calls
to push a message at the user mid-task (progress, a partial result, a
blocker that needs eyes). Messages land in regin's per-session Messages
tab and the cross-session Inbox; high-severity ones can fan out to a
webhook (ntfy / Slack / phone) so a background run still reaches you.

Why this server is thin: a stdio MCP server is *session-blind* — it never
learns which Claude Code session invoked it. regin's PostToolUse hook is
the component that knows the session, so persistence + webhook dispatch
live there (`hook_manager/handlers/post_tool_trace` →
`lib.agent_messages.store`). This server's job is to (a) declare the typed
parameter schema the model fills in, which the hook then reads off the
tool input, and (b) acknowledge the call. It deliberately imports no regin
internals so it starts instantly and can't break on a DB hiccup.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("send-to-user")


@mcp.tool()
def send_to_user(
    message: str,
    type: str = "progress",
    title: str = "",
    key: str = "",
    links: list[str] | None = None,
    supersedes: str = "",
) -> str:
    """Display a message directly to the user, mid-task.

    Use for progress updates, partial results, or content the user must
    see exactly as written before the task finishes. The message persists
    in regin's Messages tab and cross-session Inbox.

    Args:
        message: The body to show the user. Markdown is rendered.
        type: Severity / intent, lowest → highest:
            "progress" (default) · "note" · "lesson" · "result" ·
            "summary" · "warning" · "blocker". Drives inbox styling and
            whether the message fans out to the configured webhook.
            "lesson" additionally saves the message into regin's
            cross-session agent memory, so send one whenever you learn
            something a future session should know.
        title: Optional short heading for the message card.
        key: Optional supersede key, scoped to this session. Re-sending
            with the same key updates that message in place instead of
            stacking a new card — use it for a single progress line that
            advances ("building… 40%" → "done"), so the feed stays clean.
        links: Optional list of file paths or URLs to surface alongside
            the message (e.g. the file you changed, a PR, a failing test).
        supersedes: Optional memory id. Only meaningful with type="lesson":
            instead of inserting a fresh memory, retire that memory
            (status=retired, chained via superseded_by) and replace it with
            this lesson — the non-destructive way to correct or refresh a
            stale memory. Ignored if the id doesn't resolve to a memory.

    Returns:
        "delivered" once the call is acknowledged. Persistence is handled
        out-of-band by regin's hook, so this returns immediately.
    """
    return "delivered"


if __name__ == "__main__":
    mcp.run()
