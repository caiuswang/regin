"""Repo-root pytest fixtures — the isolation floor for EVERY testpath.

`pyproject.toml` lists two testpaths (`tests`, `hook_manager/tests`). A
conftest under `tests/` cannot reach the second one, so the fixtures that
must hold for *all* tests — DB isolation, no outbound ingest, no external
agent spawns — live here at the root instead.

Each fixture below closes a channel through which a test run was observed
writing to the developer's real `db/regin.db`:

- `tmp_db` / `tmp_memory_db`  — direct ORM/sqlite writes from the pytest
  process. These moved up from `tests/conftest.py` so `hook_manager/tests`
  (which had no DB fixture at all) is covered too.
- `_block_ingest_transport`   — `hook_plugin.post_event` HTTP-POSTing to
  whatever dev server is listening on the configured port. The pytest
  process cannot monkeypatch that server's `DB_PATH`; it is a different
  process holding the real DB, so the write had to be stopped at the
  transport.
- `_no_external_agent_spawn`  — `subprocess.run`/`Popen` launching a real
  coding agent. `test_reflect_endpoint` reached this with `dry_run=True`,
  because dry_run gates memory writes, not the LLM call.
- `_block_notify_transport`   — the badge-push loopback POST fired by every
  `record_message` / drift write, which reaches the dev server on
  `settings.web_port` for the same reason `_block_ingest_transport` exists.
"""

from __future__ import annotations

import sqlite3
import subprocess
import threading
from pathlib import Path

import pytest

from conftest_support import (
    ExternalAgentSpawnBlocked, SpawnGuard, spawning_modules,
)


# ── DB isolation (applies to every testpath) ──────────────────

@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch) -> Path:
    """Isolated SQLite file applied to every test (autouse).

    `lib.orm.engine.DB_PATH` and the `lib.orm` engine cache point at a fresh
    file under the test's `tmp_path`. Schema.sql is applied so any table the
    code might touch exists.

    Autouse because forgetting to declare it leaks rows into the developer's
    real DB. Note the guarantee is scoped to *this* process: a test that
    reaches the network, or spawns a child, escapes it — hence the two
    fixtures below.
    """
    db_path = tmp_path / "test.db"

    schema_path = Path(__file__).resolve().parent / "db" / "schema.sql"
    if schema_path.exists():
        conn = sqlite3.connect(str(db_path))
        try:
            conn.executescript(schema_path.read_text())
            conn.commit()
        finally:
            conn.close()

    import lib.orm.engine as _db_module
    monkeypatch.setattr(_db_module, "DB_PATH", str(db_path))

    from lib.orm import engine as _engine_module
    _engine_module.dispose_engine()

    yield db_path

    _engine_module.dispose_engine()


@pytest.fixture(autouse=True)
def tmp_memory_db(tmp_path, monkeypatch) -> Path:
    """Isolated agent-memory DB per test (autouse), mirroring `tmp_db`.

    Dense recall is pinned off so no test loads the SkillRouter models
    implicitly — tests that exercise the dense leg inject a stub
    EmbeddingProvider instead. The auto-inject server leg is pinned off too
    so no test makes a live HTTP call to a dev server that happens to be on
    the configured port; its own tests stub `urlopen`.
    """
    db_path = tmp_path / "memory_test.db"
    from lib.settings import AgentMemoryConfig, settings
    # Reset the whole block to pristine model defaults rather than mutating
    # the live instance, so a developer's customized `config/settings.json`
    # can't leak into tests that read the singleton.
    fresh = AgentMemoryConfig()
    fresh.db_path = db_path
    fresh.dense_enabled = False
    fresh.inject_dense_via_server = False
    monkeypatch.setattr(settings, "agent_memory", fresh)

    import lib.memory as memory
    from lib.memory.engine import dispose_memory_engine
    dispose_memory_engine()
    memory.reset_store()

    yield db_path

    dispose_memory_engine()
    memory.reset_store()


# ── Outbound ingest (cross-process channel) ───────────────────

class _BlockedResponse:
    """Stands in for a 2xx ingest response without touching the network."""

    def read(self) -> bytes:
        return b'{"ok": true}'

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> bool:
        return False


@pytest.fixture(autouse=True)
def _block_ingest_transport(monkeypatch):
    """Sever `hook_plugin`'s HTTP transport for the whole suite.

    Reporting success (rather than a network error) is deliberate: it keeps
    `post_event`'s single-attempt happy path, so no test pays the retry
    backoff or writes an `ingest-errors.jsonl` entry for a failure we
    manufactured.

    Patching `_NO_PROXY_OPENER.open` — the same attribute the retry/backoff
    tests patch — is what makes this safe: their per-test `setattr` is
    applied after this one and therefore wins, so those tests still exercise
    the real retry logic against their own stub.
    """
    from lib import hook_plugin
    monkeypatch.setattr(
        hook_plugin._NO_PROXY_OPENER, 'open',
        lambda _req, timeout=None: _BlockedResponse(),
    )
    # In-process patching cannot reach a child process, and several tests
    # spawn one that runs the real hook handlers (`python -m hook_manager`).
    # Point the inherited env at an unroutable port so a child's own
    # `post_event` fails fast instead of finding the dev server.
    monkeypatch.setenv('REGIN_INGEST_BASE_URL', 'http://127.0.0.1:1')


@pytest.fixture(autouse=True)
def _block_notify_transport(monkeypatch):
    """Sever the badge-push loopback POST for the whole suite.

    Every inbox write and every drift write pings the dashboard so it can
    recompute the nav badges. That ping is addressed by port, not by DB path,
    so an unguarded run hits whatever `regin serve` the developer has up —
    the same escape `_block_ingest_transport` closes for span ingest.

    Patched at the transport (`_post_notify`), not at `notify_counts_changed`:
    producers import the latter by value, so rebinding the name on the module
    would leave their bindings pointing at the real function.
    """
    from lib.notifications import notify
    monkeypatch.setattr(notify, '_post_notify', lambda _port: None)
    # In-process patching cannot reach a child process, and several tests
    # spawn one that runs the real hook handlers. Point its port at a closed
    # one so the child's own probe fails instead of finding the dev server.
    monkeypatch.setenv('REGIN_WEB_PORT', '1')


# ── External agent spawns ─────────────────────────────────────

@pytest.fixture(autouse=True)
def _no_external_agent_spawn(monkeypatch):
    """Make an external-agent spawn impossible, two layers deep.

    Layer 1 — empty `topic_proposal_external_agents`: the single config every
    spawn path resolves through (memory dreamer, grader judge, topic
    proposals). Each already documents "none configured → return None", so
    this reproduces a pristine checkout rather than inventing a new state.

    Layer 2 — a subprocess guard scoped to every module that launches one,
    for the case where a test configures its own agent (several fixtures do)
    and then reaches a code path that spawns for real. Layer 1 alone is not
    enough: any test that sets `topic_proposal_external_agents` overrides it.

    Layer 3 — a `threading.excepthook`, because two of these spawn on a
    daemon thread (`proposal_review._spawn_review_agent`,
    `external_jobs`). An exception raised there never reaches pytest, so
    without this the guard could fire and the test would still pass.
    """
    from lib.settings import settings
    monkeypatch.setattr(settings, "topic_proposal_external_agents", {})

    for module in spawning_modules():
        monkeypatch.setattr(module, "subprocess", SpawnGuard())

    escaped: list[str] = []
    prior_hook = threading.excepthook

    def _record(args):
        if issubclass(args.exc_type, ExternalAgentSpawnBlocked):
            escaped.append(str(args.exc_value))
        return prior_hook(args)

    monkeypatch.setattr(threading, "excepthook", _record)
    yield
    assert not escaped, (
        "an external-agent spawn was blocked on a background thread; the "
        "calling test would otherwise have passed silently:\n  "
        + "\n  ".join(escaped)
    )


@pytest.fixture
def allow_subprocess_spawn(_no_external_agent_spawn, monkeypatch):
    """Opt out of the layer-2 spawn guard.

    For tests that deliberately launch a *harmless* binary (`pwd`) to
    exercise the subprocess plumbing itself — argv assembly, cwd
    resolution, tilde expansion. Requesting this fixture is the explicit
    statement that the spawn is intended; the guard stays on everywhere
    else. Depends on the guard fixture so it is applied afterwards and
    therefore wins.
    """
    for module in spawning_modules():
        monkeypatch.setattr(module, "subprocess", subprocess)
