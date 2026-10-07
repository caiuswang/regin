"""A fake `kimi acp` subprocess for the client tests.

Speaks just enough ACP (newline-delimited JSON-RPC over stdio) to exercise
`lib.agent_acp.client`: initialize handshake, session/new, and a session/prompt
that streams chunks then acks. Set FAKE_ACP_BUSY=1 to answer every prompt with
the turn.agent_busy error the real server raises mid-turn.

Lifecycle knobs: FAKE_ACP_STOP_REASON sets the prompt ack's stopReason,
FAKE_ACP_NO_IMAGE=1 declares no image prompt support, FAKE_ACP_REJECT_MODEL=1
errors session/set_model, and FAKE_ACP_RECORD=<path> appends every request
received to that file, one JSON object per line.
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
    if stall:
        import time
        time.sleep(stall)
    for word in ("ac", "p", "-", "ok"):
        _update(sid, {"sessionUpdate": "agent_message_chunk",
                      "content": {"type": "text", "text": word}})
    _update(sid, {"sessionUpdate": "agent_response_complete",
                  "content": {"type": "text",
                              "text": _prompt_text(params)}})
    open_turns.discard(sid)
    _respond(msg_id, {"stopReason": os.environ.get("FAKE_ACP_STOP_REASON")
                      or "end_turn"})


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
    _respond(msg_id, {"protocolVersion": 1,
                      "agentCapabilities": caps,
                      "authMethods": [], "modes": MODES})


def _session_new(_params, msg_id, state):
    n = len(state.sessions) + 1
    sid = f"session_fake{n:04d}"
    state.sessions[sid] = 0
    _respond(msg_id, {"sessionId": sid, "modes": MODES})


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
    "session/set_mode": lambda _p, msg_id, _s: _respond(msg_id, {}),
    "session/set_model": _session_set_model,
    "session/prompt": _session_prompt,
    "session/cancel": lambda *_args: None,  # notification: nothing to answer
}


def main():
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
