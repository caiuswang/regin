"""Shared pytest fixtures for the regin test suite.

Intentionally minimal — each fixture isolates a single external
dependency so tests can opt in. See plan phase A.2 at
`~/.claude/plans/staged-jingling-abelson.md` for the design notes.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
from pathlib import Path
from typing import Iterator

import pytest


# ── Span ingest isolation ─────────────────────────────────────

_TEST_TRACE_IDS: set[str] = set()


@pytest.fixture(autouse=True)
def _mark_test_spans(monkeypatch):
    """Stamp REGIN_TRACE_TEST=1 so spans emitted during a test land in
    the DB tagged `is_test=1` and stay out of the user's default
    sessions view, and record every trace_id posted so
    `_end_test_sessions_at_teardown` can close them at suite end.

    We don't block POSTs — tests that want to assert on persisted spans
    rely on them actually landing. See the matching fixture in
    `hook_manager/tests/conftest.py` for the full rationale.
    """
    monkeypatch.setenv('REGIN_TRACE_TEST', '1')
    from lib import hook_plugin
    original = hook_plugin.post_event

    def _recording(endpoint, data, agent_type=None):
        rows = data if isinstance(data, list) else [data]
        for row in rows:
            tid = isinstance(row, dict) and row.get('trace_id')
            if isinstance(tid, str) and tid:
                _TEST_TRACE_IDS.add(tid)
        return original(endpoint, data, agent_type)

    monkeypatch.setattr(hook_plugin, 'post_event', _recording)


@pytest.fixture(scope='session', autouse=True)
def _end_test_sessions_at_teardown():
    """At suite teardown, emit `session.end` for every trace_id posted
    during the run so the resulting sessions rows flip from 'active'
    to 'ended' instead of cluttering the (include_tests=true) view as
    perpetually-active test sessions."""
    yield
    if not _TEST_TRACE_IDS:
        return
    import os as _os
    from lib import hook_plugin
    # Per-test monkeypatch reverts before this teardown runs, so re-set
    # REGIN_TRACE_TEST=1 so the closing session.end spans land tagged
    # (otherwise teardown would create fresh is_test=0 sessions).
    _os.environ['REGIN_TRACE_TEST'] = '1'
    try:
        for tid in sorted(_TEST_TRACE_IDS):
            try:
                hook_plugin.post_span(
                    trace_id=tid,
                    name='session.end',
                    attributes={'reason': 'test_teardown'},
                )
            except Exception:
                pass
    finally:
        _os.environ.pop('REGIN_TRACE_TEST', None)


# ── Rule-engine setup ─────────────────────────────────────────

@pytest.fixture
def configured_grit_engine(tmp_path, monkeypatch):
    """Make `rule_engines.get('grit')` succeed for tests that assume a
    grit engine exists in the user's environment.

    Creates a minimal tmp grit dir with one sample Java rule and pins
    `settings.rule_engines` to an explicit Java grit engine pointing at
    it, so the fixture is independent of whatever `config/settings.json`
    configures (which may target a different language). Without the pin,
    tests that exercise `rule_engines.get('grit')`, the `grit-rules`
    auto-skill, or Java applicable-files matching inherit ambient config
    and break whenever it changes.

    Apply at module scope with `pytestmark = pytest.mark.usefixtures(...)`
    in test files that need it.
    """
    grit_root = tmp_path / 'grit'
    patterns_dir = grit_root / 'patterns' / 'java'
    patterns_dir.mkdir(parents=True)
    (patterns_dir / 'sample.grit').write_text(
        '// @rule id=test_rule\n'
        '// @rule layer=service-impl\n'
        '// @rule triggers=*ServiceImpl.java\n'
        '// @rule severity=warn\n'
        '// @rule summary=Test rule for fixtures\n'
        'pattern test_rule() {\n'
        '  // body\n'
        '}\n'
    )
    from lib import settings as _s
    monkeypatch.setattr(_s.settings, 'grit_dir', grit_root)
    monkeypatch.setattr(
        _s.settings, 'rule_engines',
        [_s.RuleEngineConfig(
            id='grit', kind='grit',
            grit_dir=grit_root, language_ids=('java',),
        )],
    )
    return grit_root


# ── DB fixtures ───────────────────────────────────────────────

@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch) -> Path:
    """Isolated SQLite file applied to every test (autouse).

    `lib.orm.engine.DB_PATH` and the `lib.orm` engine cache point at a fresh
    file under the test's `tmp_path`. Schema.sql is applied so any
    table the code might touch exists.

    Autouse because forgetting to declare this fixture leaks rows
    into the developer's real DB — that bit us pre-Phase-A when
    `fake_git_repo` didn't depend on it. The cost is small (~3ms
    schema seed per test); the safety is absolute: no test can
    write to the prod DB by accident.

    The legacy `trace_db` fixture in `tests/test_trace_ingest.py`
    predates this and applies a narrower schema to a separate tmp
    file; it stacks harmlessly on top of `tmp_db` (its patch wins
    inside the trace tests).
    """
    db_path = tmp_path / "test.db"

    # Apply the full schema so any table the code might touch exists.
    schema_path = (
        Path(__file__).resolve().parent.parent / "db" / "schema.sql"
    )
    if schema_path.exists():
        conn = sqlite3.connect(str(db_path))
        try:
            conn.executescript(schema_path.read_text())
            conn.commit()
        finally:
            conn.close()

    import lib.orm.engine as _db_module
    monkeypatch.setattr(_db_module, "DB_PATH", str(db_path))

    # Invalidate any cached SQLAlchemy engine so the next SessionLocal
    # picks up the new URL.
    from lib.orm import engine as _engine_module
    _engine_module.dispose_engine()

    yield db_path

    _engine_module.dispose_engine()


@pytest.fixture(autouse=True)
def tmp_memory_db(tmp_path, monkeypatch) -> Path:
    """Isolated agent-memory DB per test (autouse), mirroring `tmp_db`'s
    guarantee for the main DB: no test can write the real
    `db/regin_memory.db` by accident. The memory engine self-initializes
    its schema on first use, so pointing the setting at a tmp file is the
    whole setup. Dense recall is pinned off so no test loads the
    SkillRouter models implicitly — tests that exercise the dense leg
    inject a stub EmbeddingProvider instead. The auto-inject server leg
    is pinned off too so no test makes a live HTTP call to a dev server
    that happens to be on :8321 — its own tests stub `urlopen`."""
    db_path = tmp_path / "memory_test.db"
    from lib.settings import settings
    monkeypatch.setattr(settings.agent_memory, "db_path", db_path)
    monkeypatch.setattr(settings.agent_memory, "dense_enabled", False)
    monkeypatch.setattr(settings.agent_memory, "inject_dense_via_server", False)

    import lib.memory as memory
    from lib.memory.engine import dispose_memory_engine
    dispose_memory_engine()
    memory.reset_store()

    yield db_path

    dispose_memory_engine()
    memory.reset_store()


# ── Config isolation ──────────────────────────────────────────

@pytest.fixture
def tmp_config_dir(tmp_path, monkeypatch) -> Path:
    """Isolated `$REGIN_DATA_DIR` + `XDG_DATA_HOME` so path settings
    resolve into `tmp_path` for the test."""
    monkeypatch.setenv("REGIN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    # Re-import lib.settings so the new env picks up.
    from lib.settings import reload_settings
    reload_settings()
    yield tmp_path
    # Pop the env overrides BEFORE the final reload so the rebuilt
    # singleton picks up the real user env rather than this fixture's
    # shadow copy. monkeypatch's own teardown is otherwise deferred
    # until after this function returns, leaving `settings` pinned to
    # a tmp_path that pytest is about to delete.
    for key in ("REGIN_DATA_DIR", "XDG_DATA_HOME"):
        os.environ.pop(key, None)
    reload_settings()


# ── Flask client ──────────────────────────────────────────────

@pytest.fixture
def flask_client(tmp_db) -> Iterator:
    """Flask test client wired to `tmp_db`, authenticated as an editor.

    The app gates every /api/ route behind a valid JWT (see
    `web.app._install_auth_gate`), so the default client carries an editor
    token in `environ_base` — this models "a logged-in user is browsing"
    and keeps read/editor-mutation tests working. Tests that need a
    different identity pass their own `Authorization` header, which
    overrides `environ_base` per request. Tests that assert
    unauthenticated behaviour (401) use `anon_client` instead.
    """
    from web.app import create_app
    from lib.auth import create_token
    app = create_app()
    app.config["TESTING"] = True
    token = create_token(1, "test-editor", "editor")
    with app.test_client() as client:
        client.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        yield client


@pytest.fixture
def anon_client(tmp_db) -> Iterator:
    """Unauthenticated Flask test client — sends no Authorization header.

    Use for tests that exercise the auth gate itself or per-route auth
    decorators (missing/insufficient credentials → 401/403), and for the
    public bootstrap endpoints (/api/auth/login, register, me)."""
    from web.app import create_app
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


# ── Git fixture ───────────────────────────────────────────────

@pytest.fixture
def fake_git_repo(tmp_path, tmp_db) -> Path:
    """Initialise a minimal git repo at `tmp_path` with one committed
    file so `lib.sync.git_ops` calls return sensible data.

    Depends on `tmp_db` so tests that exercise the topic-proposal
    pipeline (which now writes `GraphSnapshot` rows on every accept)
    get DB isolation without each test having to declare it.

    Skips if `git` is not on PATH (tests should `pytest.importorskip`
    via the fixture body rather than here — Python's import machinery
    doesn't notice system-level tools).
    """
    if not shutil.which("git"):
        pytest.skip("git not installed")

    from subprocess import check_call, DEVNULL

    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    check_call(["git", "init", "-q", "-b", "main", str(tmp_path)], env=env,
               stdout=DEVNULL, stderr=DEVNULL)
    # Persist the identity in the repo config so subsequent `git commit`
    # calls from test bodies don't need to thread `env=` through every
    # subprocess call. Without this, machines whose hostname produces an
    # invalid auto-detected email (e.g. macOS `bogon.(none)`) fail.
    check_call(["git", "-C", str(tmp_path), "config", "user.email", "t@t"])
    check_call(["git", "-C", str(tmp_path), "config", "user.name", "t"])
    (tmp_path / "README.md").write_text("baseline\n")
    check_call(["git", "-C", str(tmp_path), "add", "."], env=env)
    check_call(["git", "-C", str(tmp_path), "commit", "-q", "-m", "init"],
               env=env, stdout=DEVNULL, stderr=DEVNULL)
    return tmp_path


# ── Topic proposal provider stub ──────────────────────────────


def _stub_repo_path(repo) -> str:
    """A real repo-relative file path so the stub topic survives the
    apply pipeline's dead-ref validation. Prefers README.md (which
    `fake_git_repo` creates)."""
    repo = Path(repo)
    if (repo / "README.md").exists():
        return "README.md"
    for path in sorted(repo.rglob("*")):
        if path.is_file() and ".regin" not in path.parts and ".git" not in path.parts:
            return path.relative_to(repo).as_posix()
    return "README.md"


def _stub_topic(path: str) -> dict:
    return {
        "id": "stub-topic",
        "label": "Stub Topic",
        "aliases": [],
        "intent": "Stub topic for test scaffolding.",
        "status": "active",
        "refs": [{"path": path, "role": "implementation"}],
        "edges": [],
        "commands": [],
        "include_globs": [],
        "exclude_globs": [],
        "evidence_paths": [path],
    }


class _InlineThread:
    """Run background proposal jobs inline so the async start_external_*
    paths are deterministic under test (no real daemon thread)."""

    def __init__(self, *, target=None, kwargs=None, daemon=None, **_):
        self._target = target
        self._kwargs = kwargs or {}

    def start(self):
        if self._target is not None:
            self._target(**self._kwargs)


@pytest.fixture
def stub_proposal_provider(monkeypatch):
    """Stub the external-agent drafting seam so proposal-lifecycle tests
    (create / accept / merge / regenerate / web endpoints) run without a
    real agent subprocess.

    `_draft_proposal` returns a canned topic + wiki referencing a real
    repo file, and background jobs run inline for deterministic asserts.
    """
    def _stub_draft(
        *, repo, out_dir, proposal_id, topic_request=None, scope="all",
        agent=None, prior_draft=None, prompt_templates=None,
    ):
        del out_dir, proposal_id, topic_request, agent, prior_draft, prompt_templates
        proposals = {
            "version": 1,
            "repo": Path(repo).resolve().name,
            "scope": scope,
            "generated_at": "2024-01-01T00:00:00+00:00",
            "status": "draft",
            "topics": [_stub_topic(_stub_repo_path(repo))],
            "notes": ["stub provider"],
        }
        wiki = "# Stub Wiki\n\nStub proposal wiki for tests.\n"
        return proposals, wiki

    monkeypatch.setattr("lib.topics.proposals.core_io._draft_proposal", _stub_draft)
    monkeypatch.setattr("lib.topics.proposals.external_jobs._draft_proposal", _stub_draft)
    monkeypatch.setattr("lib.topics.proposals.external_jobs.threading.Thread", _InlineThread)
    return _stub_draft
