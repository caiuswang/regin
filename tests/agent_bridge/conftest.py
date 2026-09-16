"""Agent-bridge test wiring.

The accept list now prefers a Claude Agent SDK handshake, which spawns a real
`claude` child. No test may pay that (or depend on the host's real command
catalog), so the handshake is stubbed out by default and the per-root TTL
cache is emptied between tests; the SDK-path tests re-patch
`commands._server_info_commands` with their own stub.

The `@`-suggestion directory-listing cache is emptied on the same boundary:
it is keyed by absolute path and TTL'd, so a route test hitting a `tmp_path`
a previous test also used would otherwise read that test's listing.

Root resolution now also asks the SDK tier (`agent_runs`) when no pane row
answers, and the trace's own `sessions.cwd` when neither does — both DB reads,
so a test that took no database gets each stubbed to "unknown". Requesting
`tmp_db` (directly or through `flask_client`) opts back into the real query
against the temp DB.
"""

from __future__ import annotations

import pytest

from lib.agent_bridge import commands, files, roots
from lib.agent_sdk import store as sdk_store


@pytest.fixture(autouse=True)
def _no_sdk_handshake(monkeypatch):
    async def _unavailable(root):
        raise RuntimeError("SDK disabled in tests")

    commands._sdk_cache.clear()
    monkeypatch.setattr(commands, "_server_info_commands", _unavailable)
    yield
    commands._sdk_cache.clear()


@pytest.fixture(autouse=True)
def _no_sdk_run(request, monkeypatch):
    """No SDK run behind a DB-free test's trace id — and no DB read for it."""
    if "tmp_db" in request.fixturenames:
        return
    monkeypatch.setattr(sdk_store, "find_run", lambda session_id: None)


@pytest.fixture(autouse=True)
def _no_traced_cwd(request, monkeypatch):
    """Same deal for the trace's own cwd: a `sessions` read a DB-free test
    cannot make, and which would otherwise reach the host's real database."""
    if "tmp_db" in request.fixturenames:
        return
    monkeypatch.setattr(roots, "_traced_session_cwd", lambda trace_id: None)


@pytest.fixture(autouse=True)
def _no_reload_marker(request, monkeypatch):
    """Same deal for the reload marker: a span read a DB-free test can't make."""
    if "tmp_db" in request.fixturenames:
        return
    monkeypatch.setattr(commands, "_reload_marker", lambda trace_id: "")


@pytest.fixture(autouse=True)
def _clear_listing_cache():
    files._listing_cache.clear()
    yield
    files._listing_cache.clear()
