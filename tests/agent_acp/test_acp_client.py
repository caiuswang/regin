"""Feature #140 — the `kimi acp` client (lib/agent_acp).

Driven against a fake ACP server subprocess (fake_acp_server.py) so the suite
stays hermetic; the real-binary smoke test lives in tests/tasks/test_acp_launch.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from lib.agent_acp.client import AcpClient, AcpTurnBusy

FAKE = str(Path(__file__).parent / "fake_acp_server.py")


@pytest.fixture
def acp():
    client = AcpClient(cmd=[sys.executable, FAKE], env={"FAKE_ACP_BUSY": "0"})
    client.connect()
    try:
        yield client
    finally:
        client.close()


def test_kimi_acp_initialize_and_session_new_returns_id(acp, tmp_path):
    result = acp.initialize()
    assert result["protocolVersion"] == 1
    sid = acp.session_new(cwd=str(tmp_path))
    assert sid.startswith("session_")


def test_kimi_acp_prompt_roundtrip_streams_chunks_and_stop_reason(acp,
                                                                  tmp_path):
    acp.initialize()
    sid = acp.session_new(cwd=str(tmp_path))
    chunks: list[str] = []
    result = acp.session_prompt(
        sid, "Reply with exactly: acp-ok",
        on_chunk=lambda text: chunks.append(text))
    assert chunks == ["ac", "p", "-", "ok"]
    assert result.stop_reason == "end_turn"


def test_kimi_acp_busy_prompt_rejected_turn_agent_busy(tmp_path,
                                                       monkeypatch):
    client = AcpClient(cmd=[sys.executable, FAKE], env={"FAKE_ACP_BUSY": "1"})
    client.connect()
    try:
        client.initialize()
        sid = client.session_new(cwd=str(tmp_path))
        with pytest.raises(AcpTurnBusy):
            client.session_prompt(sid, "hello")
    finally:
        client.close()


@pytest.mark.skipif(not shutil.which("kimi"), reason="kimi CLI not installed")
def test_against_real_kimi_smoke(tmp_path):
    """Real-binary sanity: spawn `kimi acp`, one turn. Costs one API call;
    skipped on machines without the CLI (CI)."""
    client = AcpClient()
    client.connect()
    try:
        client.initialize()
        sid = client.session_new(cwd=str(tmp_path))
        result = client.session_prompt(sid, "Reply with exactly: ok")
        assert result.stop_reason == "end_turn"
    finally:
        client.close()
