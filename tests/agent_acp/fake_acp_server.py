"""A fake `kimi acp` subprocess for the client tests.

Speaks just enough ACP (newline-delimited JSON-RPC over stdio) to exercise
`lib.agent_acp.client`: initialize handshake, session/new, and a session/prompt
that streams chunks then acks. Set FAKE_ACP_BUSY=1 to answer every prompt with
the turn.agent_busy error the real server raises mid-turn.

Lifecycle knobs: FAKE_ACP_STOP_REASON sets the prompt ack's stopReason,
FAKE_ACP_NO_IMAGE=1 declares no image prompt support, FAKE_ACP_NO_LOAD=1
declares no loadSession (session/load then errors), FAKE_ACP_REJECT_MODEL=1
errors session/set_model, FAKE_ACP_TOOL=<title> announces a tool call in
progress under that title (with a usage_update) before the stall and completes
it after, and FAKE_ACP_RECORD=<path> appends every request received to that
file, one JSON object per line. FAKE_ACP_REQUIRE_ARG=<flag> exits unless argv
carries that flag; FAKE_ACP_HOOK_PROVIDER=<id> runs regin's real PostToolUse
hook (`--agent-type <id>`) for one Bash call at the start of each prompt turn.
FAKE_ACP_PERMISSION=1 asks `session/request_permission` (codebuddy's option
shape) at the start of each turn and waits for the answer, which it records;
a `cancelled` answer ends the turn with stopReason `cancelled`, as codebuddy
does. FAKE_ACP_QUESTION=1 makes that request kimi's AskUserQuestion shape
instead (question in `content`, one `allow_once` per answer, a Skip).
"""

from __future__ import annotations

import json
import os
import sys

MODES = [{"id": "default", "name": "Default"},
         {"id": "plan", "name": "Plan"},
         {"id": "auto", "name": "Auto"},
         {"id": "yolo", "name": "Yolo"}]

CAPABILITIES = {
    "loadSession": True,
    "promptCapabilities": {"image": True, "audio": False,
                           "embeddedContext": True},
    "sessionCapabilities": {"load": {}, "resume": {}, "close": {},
                            "fork": {}, "list": {}},
}


def _respond(msg_id, result):
    print(json.dumps({"jsonrpc": "2.0", "id": msg_id, "result": result}),
          flush=True)


def _error(msg_id, code, message, data=None):
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    print(json.dumps({"jsonrpc": "2.0", "id": msg_id, "error": err}),
          flush=True)


def _update(session_id, update):
    print(json.dumps({"jsonrpc": "2.0", "method": "session/update",
                      "params": {"sessionId": session_id,
                                 "update": update}}), flush=True)


def _prompt_text(params) -> str:
    return "".join(b.get("text", "")
                   for b in params.get("prompt", [])
                   if b.get("type") == "text")


def _handle_prompt(params, msg_id, sessions, busy, serial, open_turns,
                   stall):
    """One prompt turn: stream the fixed reply, then ack with stopReason."""
    sid = params.get("sessionId")
    if sid not in sessions:
        _error(msg_id, -32602, f"Unknown sessionId: {sid}")
        return
    if busy or (serial and sid in open_turns):
        _error(msg_id, -32600, "another turn is already in progress",
               {"code": "turn.agent_busy"})
        return
    open_turns.add(sid)
    _fire_hook(sid)
    if os.environ.get("FAKE_ACP_PERMISSION") == "1" \
            and _ask_permission(sid) == "cancelled":
        open_turns.discard(sid)
        _respond(msg_id, {"stopReason": "cancelled"})
        return
    tool = os.environ.get("FAKE_ACP_TOOL")
    if tool:
        _update(sid, {"sessionUpdate": "tool_call", "toolCallId": "0:fake-1",
                      "title": "Bash", "kind": "execute", "status": "pending"})
        _update(sid, {"sessionUpdate": "tool_call_update",
                      "toolCallId": "0:fake-1", "title": tool,
                      "status": "in_progress"})
        _update(sid, {"sessionUpdate": "usage_update", "used": 65536,
                      "size": 262144})
    if stall:
        import time
        time.sleep(stall)
    if tool:
        _update(sid, {"sessionUpdate": "tool_call_update",
                      "toolCallId": "0:fake-1", "status": "completed"})
    for word in ("ac", "p", "-", "ok"):
        _update(sid, {"sessionUpdate": "agent_message_chunk",
                      "content": {"type": "text", "text": word}})
    _update(sid, {"sessionUpdate": "agent_response_complete",
                  "content": {"type": "text",
                              "text": _prompt_text(params)}})
    open_turns.discard(sid)
    _respond(msg_id, {"stopReason": os.environ.get("FAKE_ACP_STOP_REASON")
                      or "end_turn"})


_PERMISSION_OPTIONS = [
    {"kind": "allow_always", "name": "Always Allow", "optionId": "allow_always"},
    {"kind": "allow_once", "name": "Allow", "optionId": "allow"},
    {"kind": "reject_once", "name": "Reject", "optionId": "reject"},
]


_QUESTION_OPTIONS = [
    {"optionId": "q0_opt_0", "name": "Approve and build", "kind": "allow_once"},
    {"optionId": "q0_opt_1", "name": "Revise first", "kind": "allow_once"},
    {"optionId": "q0_opt_2", "name": "Abandon", "kind": "allow_once"},
    {"optionId": "q0_skip", "name": "Skip", "kind": "reject_once"},
]


def _permission_params(sid: str) -> dict:
    if os.environ.get("FAKE_ACP_QUESTION") == "1":
        return {"sessionId": sid, "options": _QUESTION_OPTIONS,
                "toolCall": {"toolCallId": "0:call_ask_1",
                             "title": "AskUserQuestion",
                             "content": [{"type": "content", "content": {
                                 "type": "text", "text": "Approve the plan?"}}]}}
    return {"sessionId": sid, "options": _PERMISSION_OPTIONS,
            "toolCall": {"toolCallId": "call_perm_1",
                         "rawInput": {"command": "git worktree remove --force x"},
                         "_meta": {"codebuddy.ai/toolName": "Bash"}}}


def _ask_permission(sid: str) -> str:
    """Ask for one gated Bash call and block on the reply, as the agent
    does; every line read meanwhile is recorded. The chosen optionId, or
    `cancelled`."""
    print(json.dumps({"jsonrpc": "2.0", "id": "perm-1",
                      "method": "session/request_permission",
                      "params": _permission_params(sid)}), flush=True)
    for line in sys.stdin:
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        _record(msg)
        if msg.get("id") == "perm-1" and "method" not in msg:
            outcome = (msg.get("result") or {}).get("outcome") or {}
            return outcome.get("optionId") or outcome.get("outcome") or ""
    return "cancelled"


def _fire_hook(sid: str) -> None:
    """Run regin's real PostToolUse hook for one Bash call in session `sid`,
    as the agent CLI would mid-turn."""
    provider = os.environ.get("FAKE_ACP_HOOK_PROVIDER")
    if not provider:
        return
    import subprocess
    payload = {"hook_event_name": "PostToolUse", "session_id": sid,
               "agent_type": "cli", "tool_name": "Bash",
               "tool_use_id": "call_fake_1",
               "tool_input": {"command": "echo hi"},
               "tool_response": {"exitCode": 0, "interrupted": False}}
    subprocess.run([sys.executable, "-P", "-m", "hook_manager", "PostToolUse",
                    "--agent-type", provider],
                   input=json.dumps(payload), text=True, timeout=60,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   check=False)


class _State:
    """What the fake server remembers across requests."""

    def __init__(self):
        self.sessions: dict[str, int] = {}
        self.open_turns: set[str] = set()
        self.busy = os.environ.get("FAKE_ACP_BUSY") == "1"
        self.serial = os.environ.get("FAKE_ACP_SERIAL") == "1"
        self.stall = float(os.environ.get("FAKE_ACP_STALL", "0"))


def _initialize(_params, msg_id, _state):
    caps = json.loads(json.dumps(CAPABILITIES))
    if os.environ.get("FAKE_ACP_NO_IMAGE") == "1":
        caps["promptCapabilities"]["image"] = False
    if os.environ.get("FAKE_ACP_NO_LOAD") == "1":
        caps["loadSession"] = False
    _respond(msg_id, {"protocolVersion": 1,
                      "agentCapabilities": caps,
                      "authMethods": [], "modes": MODES})


def _session_new(_params, msg_id, state):
    n = len(state.sessions) + 1
    sid = f"session_fake{n:04d}"
    state.sessions[sid] = 0
    _respond(msg_id, {"sessionId": sid, "modes": MODES})


def _session_load(params, msg_id, state):
    """Take over an existing id, replaying its history first — as the real
    server does — so a client that mistakes replay for a live turn shows."""
    sid = params.get("sessionId")
    if os.environ.get("FAKE_ACP_NO_LOAD") == "1" or not sid:
        _error(msg_id, -32601, "Method not found: session/load")
        return
    state.sessions[sid] = 0
    _update(sid, {"sessionUpdate": "agent_message_chunk",
                  "content": {"type": "text", "text": "replayed"}})
    _respond(msg_id, {"modes": MODES})


def _session_prompt(params, msg_id, state):
    _handle_prompt(params, msg_id, state.sessions, state.busy, state.serial,
                   state.open_turns, state.stall)


def _session_set_model(_params, msg_id, _state):
    if os.environ.get("FAKE_ACP_REJECT_MODEL") == "1":
        _error(msg_id, -32602, "unknown model")
    else:
        _respond(msg_id, {})


def _record(msg):
    path = os.environ.get("FAKE_ACP_RECORD")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(msg) + "\n")


_HANDLERS = {
    "initialize": _initialize,
    "session/new": _session_new,
    "session/load": _session_load,
    "session/set_mode": lambda _p, msg_id, _s: _respond(msg_id, {}),
    "session/set_model": _session_set_model,
    "session/prompt": _session_prompt,
    "session/cancel": lambda *_args: None,  # notification: nothing to answer
}


def main():
    required = os.environ.get("FAKE_ACP_REQUIRE_ARG")
    if required and required not in sys.argv[1:]:
        sys.exit(f"fake ACP server: {required} missing from {sys.argv[1:]}")
    state = _State()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        _record(msg)
        method, msg_id, params = msg.get("method"), msg.get("id"), \
            msg.get("params") or {}
        handler = _HANDLERS.get(method)
        if handler is not None:
            handler(params, msg_id, state)
        elif msg_id is not None:
            _error(msg_id, -32601, f"methodNotFound: {method}")


if __name__ == "__main__":
    main()
