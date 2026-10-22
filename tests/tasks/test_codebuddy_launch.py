"""A codebuddy task run launches over ACP through the real launcher.

A fake `codebuddy` on PATH speaks ACP only when started with `--acp`, and
fires regin's real PostToolUse hook mid-turn, so the test follows the run
from `start_run` through the channel launcher to the hook's tool span.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from lib.orm.models.tasks import RUN_STATUS_RUNNING, TASK_STATUS_ARMED
from lib.tasks import runs

REPO_ROOT = Path(__file__).resolve().parents[2]
FAKE = REPO_ROOT / "tests" / "agent_acp" / "fake_acp_server.py"


class _Capture(BaseHTTPRequestHandler):
    bodies: list = []

    def do_POST(self):  # noqa: N802 — http.server's handler naming
        length = int(self.headers.get("Content-Length") or 0)
        _Capture.bodies.append(json.loads(self.rfile.read(length) or b"null"))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *_args):
        pass


@pytest.fixture
def ingest():
    _Capture.bodies = []
    server = HTTPServer(("127.0.0.1", 0), _Capture)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}", _Capture.bodies
    server.shutdown()


@pytest.fixture
def fake_codebuddy(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    script = bindir / "codebuddy"
    script.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" "$@"\n')
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    return script


def _tool_spans(bodies: list, name: str) -> list[dict]:
    spans = []
    for body in bodies:
        for span in body if isinstance(body, list) else [body]:
            if isinstance(span, dict) and span.get("name") == name:
                spans.append(span)
    return spans


def _wait_settled(task_id: int, timeout: float = 60) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        row = runs.latest_attempt(task_id)
        if row and row.get("status") not in (None, "armed", "running"):
            return row
        time.sleep(0.2)
    return runs.latest_attempt(task_id) or {}


def test_codebuddy_task_run_launches_over_acp(tmp_db, native_task, ingest,
                                              fake_codebuddy, tmp_path,
                                              monkeypatch):
    base_url, bodies = ingest
    channel_calls = []
    real_channel = runs._start_channel
    monkeypatch.setattr(runs, "_start_channel", lambda *a: (
        channel_calls.append(a), real_channel(*a))[1])
    monkeypatch.setattr(runs.supervisor, "launch_run", lambda *a, **k: (
        pytest.fail("codebuddy must not launch through the Claude SDK")))

    task_id = native_task(title="codebuddy task", status=TASK_STATUS_ARMED)
    runs.open_attempt(task_id, params={
        "provider": "codebuddy", "cwd": str(tmp_path), "one_shot": True,
        "env": {"FAKE_ACP_REQUIRE_ARG": "--acp",
                "FAKE_ACP_HOOK_PROVIDER": "codebuddy",
                "REGIN_INGEST_BASE_URL": base_url,
                "REGIN_INGEST_RETRIES": "1",
                "REGIN_ACTIVE_PROVIDER": "claude",
                "REGIN_DATA_DIR": str(tmp_path / "data"),
                "XDG_DATA_HOME": str(tmp_path / "data"),
                "PYTHONPATH": str(REPO_ROOT)}})

    view = runs.start_run(task_id)

    assert channel_calls, "codebuddy must launch through the channel arm"
    assert view["status"] == RUN_STATUS_RUNNING
    trace_id = view["trace_id"]
    assert trace_id.startswith("session_fake")
    _wait_settled(task_id)
    spans = _tool_spans(bodies, "tool.Bash")
    assert spans, f"no tool.Bash span reached ingest; got {bodies!r}"
    assert {s["trace_id"] for s in spans} == {trace_id}
    assert {s["attributes"].get("agent_type") for s in spans} == {"codebuddy"}


def test_codebuddy_missing_binary_refuses_launch(tmp_db, native_task,
                                                 tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    task_id = native_task(title="codebuddy task", status=TASK_STATUS_ARMED)
    runs.open_attempt(task_id, params={"provider": "codebuddy",
                                       "cwd": str(tmp_path)})

    with pytest.raises(runs.RunRefused, match="codebuddy CLI not found"):
        runs.start_run(task_id)
    assert runs.latest_attempt(task_id)["status"] == "failed"


def test_codebuddy_run_is_listed_without_hooks(tmp_db, native_task,
                                               fake_codebuddy, tmp_path):
    """D304: with no hooks installed nothing ever posts `session.start`, so
    the launch itself must leave the session row the list and the view-time
    transcript rescan resolve the provider from."""
    from lib.orm import SessionLocal
    from lib.orm.models import Session as TraceSession
    from lib.trace import live_rescan

    task_id = native_task(title="codebuddy task", status=TASK_STATUS_ARMED)
    runs.open_attempt(task_id, params={
        "provider": "codebuddy", "cwd": str(tmp_path), "one_shot": False,
        "env": {"FAKE_ACP_REQUIRE_ARG": "--acp", "FAKE_ACP_STALL": "30"}})

    trace_id = runs.start_run(task_id)["trace_id"]

    with SessionLocal() as db:
        row = db.get(TraceSession, trace_id)
    assert row is not None, "launched run has no sessions row"
    assert (row.agent_type, row.cwd, row.status) == (
        "codebuddy", str(tmp_path), "active")
    live_rescan._provider_cache.pop(trace_id, None)
    assert live_rescan._session_provider(trace_id).provider_id == "codebuddy"


@pytest.mark.parametrize("provider_id,mode", [
    ("codebuddy", "bypassPermissions"), ("kimi", "yolo")])
def test_acp_launch_sets_the_providers_unattended_mode(
        tmp_db, tmp_path, provider_id, mode):
    """D304/#303: codebuddy offers no `yolo`, so the hardcoded mode left its
    session on "Always Ask" and the first tool call cancelled the run."""
    from lib.agent_acp.launcher import launch_run
    from lib.providers.registry import build_provider

    record = tmp_path / "acp.jsonl"
    handle = launch_run("", cwd=str(tmp_path), cmd=[sys.executable, str(FAKE)],
                        env={"FAKE_ACP_RECORD": str(record)},
                        provider=build_provider(provider_id))
    try:
        sent = [json.loads(line) for line in record.read_text().splitlines()]
    finally:
        handle.close()
    assert [r["params"]["modeId"] for r in sent
            if r.get("method") == "session/set_mode"] == [mode]
