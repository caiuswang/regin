"""`regin` — the one MCP server an agent uses to talk to regin's pipeline.

It carries `send_to_user` and the project-management verbs over features,
tasks and defects, so any MCP-speaking agent — Claude Code, CodeBuddy,
WorkBuddy — reads and writes the same pipeline the CLI and the web UI do.

`send_to_user` stays hook-persisted: this process is session-blind, so the
tool only acks and the PostToolUse hook (matching `*__send_to_user`) lands the
message. Started with `--no-send-to-user` it omits that tool, which is how it
is registered for an agent whose hooks regin cannot install — there the ack
would claim a delivery that never happens.

Being session-blind is also why the write tools take `session_id` instead of
guessing one: the server's environment holds whichever session spawned it,
not the caller's.

The tools live one module per entity (`features`, `tasks`, `defects`,
`images`, plus the `pipeline` overview); this file only assembles them. Those
modules defer their regin imports into each call: the file runs as a script,
so a module-scope `lib.*` import would load the ORM before a tool is listed.
"""

from __future__ import annotations

import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from mcp.server.fastmcp import FastMCP

from lib.agent_messages.mcp_server import send_to_user
from lib.regin_mcp import defects, features, images, pipeline, tasks

SERVER_NAME = "regin"
NO_SEND_TO_USER = "--no-send-to-user"


PIPELINE_TOOLS = (pipeline.TOOLS + features.TOOLS + tasks.TOOLS
                  + defects.TOOLS + images.TOOLS)


def build_server(*, with_send_to_user: bool = True) -> FastMCP:
    server = FastMCP(SERVER_NAME)
    if with_send_to_user:
        server.add_tool(send_to_user)
    for tool in PIPELINE_TOOLS:
        server.add_tool(tool)
    return server


if __name__ == "__main__":
    build_server(with_send_to_user=NO_SEND_TO_USER not in sys.argv[1:]).run()
