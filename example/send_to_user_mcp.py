"""Deprecated location for the send_to_user MCP server.

The server graduated from a demo into a real feature; it now lives at
`lib/agent_messages/mcp_server.py` alongside the store, webhook, and inbox
that back it. This shim re-exports it so any stale registration that still
points here keeps working. Update your MCP config to the new path.
"""

from __future__ import annotations

from lib.agent_messages.mcp_server import mcp, send_to_user  # noqa: F401

if __name__ == "__main__":
    mcp.run()
