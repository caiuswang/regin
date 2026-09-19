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
- `_no_bridge_pane_registration` — `bridge_registry` claiming the developer's
  own tmux pane for a fixture session id, which silently evicted the live
  session sitting in it.
- `_block_push_transport`      — the `send_to_user` push fan-out reaching the
  developer's real webhook / Telegram / Feishu bot, because `maybe_dispatch`
  reads the live `config/settings.json` rather than a pristine one.
"""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
import threading
from pathlib import Path

import pytest

from conftest_support import (
    ExternalAgentSpawnBlocked, SpawnGuard, refuse_sdk_launch, spawning_modules,
)


# ── DB isolation (applies to every testpath) ──────────────────

@pytest.fixture(scope="session")
def _schema_template(tmp_path_factory) -> Path | None:
    """`db/schema.sql` executed once per session, as a file to copy from.

    Applying the script per test cost ~73ms — 126 DDL statements — which
    across the suite was the single largest line item in the run, larger than
    every slow test combined. Copying the built file is ~1ms.

    The schema carries no pragmas, so a byte-copy of the closed template is
    indistinguishable from having run the script: journal mode and page size
    are defaults either way, and no test mutates the template.
    """
    schema_path = Path(__file__).resolve().parent / "db" / "schema.sql"
    if not schema_path.exists():
        return None

    template = tmp_path_factory.mktemp("schema") / "template.db"
    conn = sqlite3.connect(str(template))
    try:
        conn.executescript(schema_path.read_text())
        conn.commit()
    finally:
        conn.close()
    return template


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch, _schema_template) -> Path:
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

    if _schema_template is not None:
        shutil.copyfile(_schema_template, db_path)

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
    monkeypatch.setattr(notify, '_post_notify', lambda _port, _body: None)
    # In-process patching cannot reach a child process, and several tests
    # spawn one that runs the real hook handlers. Point its port at a closed
    # one so the child's own probe fails instead of finding the dev server.
    monkeypatch.setenv('REGIN_WEB_PORT', '1')


@pytest.fixture(autouse=True)
def _block_push_transport(monkeypatch):
    """Sever outbound push-channel delivery (webhook / Telegram / Lark).

    `store.record_message` fans every write out through
    `push.registry.maybe_dispatch`, which reads the *developer's live*
    `config/settings.json`. On a machine with a configured Feishu bot a
    single full run fired 21 real chat messages — all from the
    PermissionRequest / ExitPlanMode handlers, whose `push_*_events`
    switches are off in a pristine checkout but on in a real config.

    Layer 1 restores that pristine checkout: the whole `agent_messages`
    block is replaced with model defaults, so no channel is configured,
    `active_channels()` is empty and `maybe_dispatch` returns None without
    building a payload. `tests/conftest.py::_default_feature_config` already
    does this for everything under `tests/`; every one of the 21 observed
    pushes came from `hook_manager/tests`, the testpath that conftest cannot
    reach. Reset wholesale rather than blanking the URL fields one by one so
    a channel added later is covered without touching this.

    Layer 2 severs the one network seam every channel shares, for the tests
    that configure a channel of their own and would otherwise escape layer 1.
    It reaches all three channels only because each calls
    `base.http_post_json(...)` through the module — a channel that imported
    the name directly would keep its own live binding. Silent rather than
    raising: the registry catches a channel's exception and records `failed`,
    so a raise would not surface as a test failure — it would only rewrite
    `webhook_status`. Tests that assert on payloads patch this same attribute
    per-test, which is applied afterwards and wins.
    """
    from lib.settings import AgentMessagesConfig, settings
    monkeypatch.setattr(settings, "agent_messages", AgentMessagesConfig())

    from lib.agent_messages.push import base as push_base
    monkeypatch.setattr(
        push_base, "http_post_json",
        lambda _url, _payload, *, timeout=None, headers=None: None,
    )


@pytest.fixture(autouse=True)
def _no_bridge_pane_registration(monkeypatch):
    """Keep the suite out of the agent-bridge pane registry.

    `bridge_registry` reads the pane triple from `os.environ`, not from the
    hook payload, so a run inside the developer's own tmux pane registers
    `bridge_panes` rows for the replayed FIXTURE session ids against that
    real pane. `tmp_db` cannot stop it: `test_replay` also spawns
    `python -m hook_manager`, and the child is a fresh process holding the
    real `DB_PATH` — the same cross-process escape `_block_ingest_transport`
    exists for.

    The rows are not inert. Occupancy is "newest reachable registration on
    the pane whose session has not ended", and a fixture id never gets a
    `sessions` row (this file severs ingest), so the LEFT JOIN reads its
    absent `ended_at` as NULL — a live claimant. It therefore EVICTS the
    session actually sitting in the pane, whose `bridge_reachable` goes
    false and takes the trace view's follow-up composer and terminal node
    down with it, until that session's next turn event re-upserts its row.
    A long-running Bash emits no turn event, so the outage lasts as long as
    the command does.

    Pinned as env rather than a patched function so it reaches those child
    processes, and because `REGIN_BRIDGE` is the handler's own first guard:
    off, `_register_pane` returns before the tmux query and before any
    write, which is exactly the pristine-checkout state.
    """
    monkeypatch.setenv('REGIN_BRIDGE', '0')


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

    Layer 4 — `lib.agent_sdk.client.new_client`, the Agent-SDK tier's single
    door to the raw SDK. That tier launches through the SDK's own machinery
    rather than a `subprocess` call this repo makes, so layer 2 is blind to
    it; a test that enables the tier and reaches a launch would otherwise
    start a real agent.

    Layer 5 — `supervisor._on_done`, layer 4's asyncio twin. A fire-and-forget
    launch puts the run on the shared loop, so layer 4's exception lands in a
    Future whose done-callback only logs it. Same failure mode layer 3 exists
    to close for daemon threads.
    """
    from lib.settings import settings
    monkeypatch.setattr(settings, "topic_proposal_external_agents", {})

    for module in spawning_modules():
        monkeypatch.setattr(module, "subprocess", SpawnGuard())

    from lib.agent_sdk import client as agent_sdk_client
    monkeypatch.setattr(agent_sdk_client, "new_client", refuse_sdk_launch)

    escaped: list[str] = []

    # Layer 5, the asyncio twin of layer 3: a fire-and-forget launch schedules
    # the run on the shared loop, where the guard's exception lands in a Future
    # that `_on_done` only logs. Without this the guard fires and the test
    # still passes.
    from lib.agent_sdk import supervisor as agent_sdk_supervisor
    prior_on_done = agent_sdk_supervisor._on_done

    def _record_run(trace_id, token, future):
        error = future.exception() if not future.cancelled() else None
        if isinstance(error, ExternalAgentSpawnBlocked):
            escaped.append(str(error))
        return prior_on_done(trace_id, token, future)

    monkeypatch.setattr(agent_sdk_supervisor, "_on_done", _record_run)
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
