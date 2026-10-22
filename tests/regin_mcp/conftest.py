"""Fixtures for the `regin` MCP server's tests: the server itself, a caller
that goes through FastMCP's own dispatch, and image files on disk."""

from __future__ import annotations

import asyncio
import base64
import json
import urllib.error

import pytest

from lib.auth import create_token
from lib.orm import SessionLocal
from lib.orm.models.sync import Repo
from lib.regin_mcp import server

EDITOR = {"Authorization": f"Bearer {create_token(2, 'ed', 'editor')}"}
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
GIF = b"GIF89a" + b"\x00" * 16


def call_tool(mcp, name: str, **args):
    """Through FastMCP's own dispatch, so argument validation and the error
    path are the ones a client gets."""
    out = asyncio.run(mcp.call_tool(name, args))
    if isinstance(out, tuple):
        return out[1]["result"]
    return json.loads(out[0].text)


@pytest.fixture(autouse=True)
def _no_regin_server(monkeypatch):
    """`task_finish` reports to the server first; no test may reach a real
    one, so by default nothing is listening and it settles in-process."""
    def refused(*_a, **_k):
        raise urllib.error.URLError(ConnectionRefusedError())
    monkeypatch.setattr("lib.tasks.finish_report.open_loopback", refused)


@pytest.fixture
def mcp():
    return server.build_server()


@pytest.fixture
def call(mcp):
    return lambda name, **args: call_tool(mcp, name, **args)


@pytest.fixture
def project():
    with SessionLocal() as s:
        s.add(Repo(name="core", path="/tmp/core"))
        s.commit()
    return "core"


@pytest.fixture
def png_path(tmp_path):
    path = tmp_path / "shot.png"
    path.write_bytes(PNG)
    return str(path)


@pytest.fixture
def png_b64():
    return base64.b64encode(PNG).decode("ascii")
