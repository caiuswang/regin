"""Hook → provider attribution when the payload carries its own `agent_type`.

CodeBuddy-family payloads ship `agent_type: "cli"` (CodeBuddy's agent
category, not a provider id). The `--agent-type` flag on the installed hook
command is the only reliable provider signal, so it must win over an
unregistered payload value — and that value must never reach
`sessions.agent_type`.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from hook_manager import runner
from hook_manager.core import HookPayload
from hook_manager.handlers import pre_tool_trace, session_lifecycle
from lib import hook_plugin
from lib.settings import settings

REPO_ROOT = Path(__file__).resolve().parents[2]

WORKBUDDY_POST_TOOL = {
    "hook_event_name": "PostToolUse",
    "session_id": "wb-attr-session",
    "agent_type": "cli",
    "client": "WorkBuddy",
    "model": "fast-model",
    "tool_name": "Bash",
    "tool_use_id": "call_wb_1",
    "tool_input": {"command": "echo hi"},
    "tool_response": {"exitCode": 0, "interrupted": False},
}


@pytest.fixture
def _active_claude(monkeypatch):
    monkeypatch.setattr(settings, "active_provider", "claude")


def _payload(data: dict, flag: str | None) -> HookPayload:
    data = dict(data)
    if flag:
        data.setdefault("agent_type", flag)
        data["provider_id"] = flag
    return HookPayload.from_stdin_json(data["hook_event_name"], data)


def _provider_of(data: dict, flag: str | None) -> str:
    return _payload(data, flag).resolved_provider.provider_id


@pytest.mark.parametrize(("flag", "payload_agent_type", "expected"), [
    # Row 1 — registered flag, payload tag missing or registered: unchanged.
    ("claude", None, "claude"),
    ("kimi", "kimi", "kimi"),
    # Row 2 — registered flag, payload tag present but unregistered: the flag.
    ("workbuddy", "cli", "workbuddy"),
    ("codebuddy", "cli", "codebuddy"),
    # Row 3 — no flag, unregistered tag: model sniff, then active_provider.
    (None, "cli", "claude"),
])
def test_attribution_decision_table(_active_claude, flag, payload_agent_type, expected):
    data = {k: v for k, v in WORKBUDDY_POST_TOOL.items() if k != "agent_type"}
    if payload_agent_type:
        data["agent_type"] = payload_agent_type
    assert _provider_of(data, flag) == expected


def test_no_flag_unregistered_tag_falls_back_to_model(_active_claude):
    data = {**WORKBUDDY_POST_TOOL, "model": "kimi-k2"}
    assert _provider_of(data, None) == "kimi"


def test_runner_writes_flag_into_provider_id(monkeypatch, _active_claude):
    seen: list[HookPayload] = []

    class _Spy:
        name = "spy"
        priority = 0

        def matches(self, payload):
            seen.append(payload)
            return False

    monkeypatch.setattr(runner, "_stash_agent_type", lambda _v: None)
    runner.run("PostToolUse", [_Spy()], json.dumps(WORKBUDDY_POST_TOOL),
               io.StringIO(), agent_type="workbuddy")

    assert seen, "handler matching never ran"
    assert seen[0].raw["provider_id"] == "workbuddy"
    assert seen[0].raw["agent_type"] == "cli"
    assert seen[0].resolved_provider.provider_id == "workbuddy"


@pytest.mark.parametrize(("flag", "expected"), [("workbuddy", "workbuddy"), (None, "claude")])
def test_session_start_never_records_cli(_active_claude, flag, expected):
    data = {**WORKBUDDY_POST_TOOL, "hook_event_name": "SessionStart"}
    assert session_lifecycle._session_agent_type(_payload(data, flag)) == expected


def test_pre_tool_span_carries_provider_not_cli(monkeypatch, _active_claude):
    spans: list[dict] = []
    monkeypatch.setattr(hook_plugin, "post_event", lambda _ep, span, **_kw: spans.append(span))
    hook_plugin.set_active_agent_type("workbuddy")
    try:
        data = {**WORKBUDDY_POST_TOOL, "hook_event_name": "PreToolUse"}
        pre_tool_trace._emit_pending(_payload(data, "workbuddy"), "Bash", "call_wb_1")
    finally:
        hook_plugin.set_active_agent_type(None)
    assert spans and spans[0]["attributes"]["agent_type"] == "workbuddy"


class _Capture(BaseHTTPRequestHandler):
    bodies: list = []

    def do_POST(self):  # noqa: N802 — http.server's handler naming
        length = int(self.headers.get("Content-Length") or 0)
        _Capture.bodies.append((self.path, json.loads(self.rfile.read(length) or b"null")))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *_args):
        pass


@pytest.fixture
def capture_server():
    _Capture.bodies = []
    server = HTTPServer(("127.0.0.1", 0), _Capture)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}", _Capture.bodies
    server.shutdown()


def _spans_named(bodies: list, name: str) -> list[dict]:
    out = []
    for _path, body in bodies:
        for span in body if isinstance(body, list) else [body]:
            if isinstance(span, dict) and span.get("name") == name:
                out.append(span)
    return out


def test_hook_with_agent_type_cli_is_attributed_by_the_flag(capture_server, tmp_path):
    base_url, bodies = capture_server
    env = {
        **os.environ,
        "REGIN_INGEST_BASE_URL": base_url,
        "REGIN_ACTIVE_PROVIDER": "claude",
        "REGIN_DATA_DIR": str(tmp_path),
        "XDG_DATA_HOME": str(tmp_path),
        "REGIN_INGEST_RETRIES": "1",
        # `-P` drops cwd from sys.path, so without this the venv's editable
        # install resolves `hook_manager` from whichever checkout installed it.
        "PYTHONPATH": str(REPO_ROOT),
    }
    proc = subprocess.run(
        [sys.executable, "-P", "-m", "hook_manager", "PostToolUse", "--agent-type", "workbuddy"],
        input=json.dumps(WORKBUDDY_POST_TOOL), capture_output=True, text=True,
        cwd=REPO_ROOT, env=env, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr

    tool_spans = _spans_named(bodies, "tool.Bash")
    assert tool_spans, f"no tool.Bash span posted; got {[p for p, _ in bodies]}"
    assert {s["attributes"].get("agent_type") for s in tool_spans} == {"workbuddy"}
    assert all(s["trace_id"] == "wb-attr-session" for s in tool_spans)
