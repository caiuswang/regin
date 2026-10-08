"""What a live ACP run is doing, folded from its `session/update` stream.

The updates below are the shapes kimi 2.1.1 sends over ACP (captured from a
real `kimi acp` turn): a tool call is announced with a bare tool name, then
retitled in progress ("Running: …", "Reading a.txt"), then completed.
"""

from __future__ import annotations

from lib.agent_acp.activity import AcpActivity


def _state(activity):
    return activity.screen_state(session_id="s1", cwd="/repo", model="k2")


def _tool(call_id, status, title=None):
    kind = "tool_call" if status == "pending" else "tool_call_update"
    update = {"sessionUpdate": kind, "toolCallId": call_id, "status": status}
    if title:
        update["title"] = title
    return update


def test_between_turns_reads_idle():
    state = _state(AcpActivity())
    assert (state.busy, state.verb, state.tool, state.elapsed_sec) == \
        (False, None, None, None)


def test_an_in_progress_tool_names_what_the_run_is_waiting_on():
    activity = AcpActivity()
    activity.turn_started()
    activity.apply(_tool("0:t1", "pending", "mcp__linear__authenticate"))
    activity.apply(_tool("0:t1", "in_progress"))
    state = _state(activity)
    assert state.busy and state.verb == "Working"
    assert state.tool == "mcp__linear__authenticate"
    assert state.elapsed_sec is not None and state.elapsed_sec >= 0
    assert (state.session_id, state.cwd, state.model) == ("s1", "/repo", "k2")


def test_the_newest_open_tool_wins_and_a_closed_one_leaves():
    activity = AcpActivity()
    activity.turn_started()
    activity.apply(_tool("0:a", "pending", "Glob"))
    activity.apply(_tool("0:a", "in_progress", "Searching *.txt"))
    activity.apply(_tool("0:b", "pending", "Read"))
    activity.apply(_tool("0:b", "in_progress", "Reading a.txt"))
    assert _state(activity).tool == "Reading a.txt"
    activity.apply(_tool("0:b", "completed"))
    assert _state(activity).tool == "Searching *.txt"
    activity.apply(_tool("0:a", "failed"))
    assert _state(activity).tool is None


def test_stream_chunks_say_thinking_or_responding():
    activity = AcpActivity()
    activity.turn_started()
    activity.apply({"sessionUpdate": "agent_thought_chunk",
                    "content": {"type": "text", "text": "hm"}})
    assert _state(activity).verb == "Thinking"
    activity.apply({"sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "ok"}})
    assert _state(activity).verb == "Responding"


def test_usage_update_is_the_context_fill():
    activity = AcpActivity()
    activity.apply({"sessionUpdate": "usage_update", "used": 21138,
                    "size": 262144})
    assert _state(activity).ctx_pct == 8


def test_a_settled_turn_forgets_calls_it_left_announced():
    """A cancelled turn never sends `completed` for the call it interrupted."""
    activity = AcpActivity()
    activity.turn_started()
    activity.apply(_tool("0:t1", "pending", "Bash"))
    activity.turn_ended()
    activity.turn_started()
    state = _state(activity)
    assert state.tool is None and state.verb == "Working"


def test_open_calls_are_named_by_the_hooks_tool_use_id():
    """kimi's ACP toolCallId is `0:tool_…`; its hooks record `tool_…`."""
    activity = AcpActivity()
    activity.turn_started()
    activity.apply(_tool("0:tool_isY65", "pending", "mcp__linear__authenticate"))
    activity.apply(_tool("0:tool_done", "pending", "Read"))
    activity.apply(_tool("0:tool_done", "completed"))
    assert activity.open_tool_use_ids() == {"tool_isY65"}
