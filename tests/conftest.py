"""Shared pytest fixtures for the regin test suite.

Intentionally minimal — each fixture isolates a single external
dependency so tests can opt in. See plan phase A.2 at
`~/.claude/plans/staged-jingling-abelson.md` for the design notes.
"""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import sys
from pathlib import Path
from typing import Iterator

import pytest


# ── `claude` CLI project-registry hygiene ─────────────────────

@pytest.fixture(scope="session", autouse=True)
def _prune_claude_tmp_projects():
    """Keep `~/.claude.json['projects']` from accumulating tmp workspaces.

    The CLI registers every cwd it is launched in and never evicts, so
    anything that spawns it in a pytest tmp_path (the tmux trace harness,
    the topics proposal/review agents) leaves a permanent entry in a file
    re-read on every startup.

    Pruning runs at session *start* as well as at teardown: a run killed
    by Ctrl-C or a crash skips teardown, and only a start-side sweep of
    all dead tmp entries makes the cleanup self-healing across runs.
    """
    _prune_dead_tmp_projects()
    yield
    _prune_dead_tmp_projects()


def _prune_dead_tmp_projects() -> None:
    root = str(Path(__file__).resolve().parents[1])
    if root not in sys.path:
        sys.path.insert(0, root)
    # Resolve the path through the script so a CLAUDE_CONFIG_DIR install prunes
    # the file the CLI actually writes, not a stale ~/.claude.json.
    from scripts.prune_claude_projects import default_config, prune  # noqa: PLC0415

    config = default_config()
    if not config.exists():
        return

    with contextlib.suppress(OSError, ValueError):
        with contextlib.redirect_stdout(io.StringIO()):
            prune(config, include_live=False, apply=True)


# ── Span ingest isolation ─────────────────────────────────────

@pytest.fixture(autouse=True)
def _mark_test_spans(monkeypatch):
    """Stamp REGIN_TRACE_TEST=1 so any span a test builds carries
    `is_test=True` (see `hook_plugin.build_span`).

    This is now a *labelling* fixture only. It used to also let ingest
    POSTs through to whatever dev server was listening, on the rationale
    that "tests which assert on persisted spans rely on them actually
    landing" — that rationale was false. Every test under `tests/` and
    `hook_manager/tests/` stubs the post seam itself; the only tests that
    genuinely need a span to land are `tests/trace/integration/`, which
    drive a separate `claude` process over tmux and read back over HTTP,
    so the in-process seam was never load-bearing for them. Meanwhile the
    pass-through wrote thousands of rows into the developer's real DB.
    The transport is severed in the root `conftest.py`.
    """
    monkeypatch.setenv('REGIN_TRACE_TEST', '1')


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
#
# `tmp_db` / `tmp_memory_db` moved to the repo-root `conftest.py` so that
# `hook_manager/tests` — the second testpath, which had no DB isolation at
# all — is covered by the same guarantee. They remain available here by
# normal conftest inheritance, so `flask_client(tmp_db)` and
# `fake_git_repo(tmp_path, tmp_db)` below still resolve.


@pytest.fixture(autouse=True)
def _default_feature_config(monkeypatch):
    """Pin developer-overridable feature blocks to their model defaults so a
    customized `config/settings.json` can't leak into tests that read the live
    `settings` singleton. Without this, the suite is only hermetic on a
    pristine checkout: e.g. `topic_evolution.auto_spawn_agents: true` makes
    proposal/regenerate tests spawn a real external agent and hang, and
    `agent_messages` retention overrides skew the settings-API contract tests.

    Resetting to model defaults only ever reproduces pristine-checkout
    behaviour, so it cannot break a test that passes in CI; a test that needs a
    non-default value still sets it explicitly (its own monkeypatch wins and
    reverts first, LIFO)."""
    from lib.settings import (AgentMessagesConfig, TopicEvolutionConfig,
                              settings)
    monkeypatch.setattr(settings, "topic_evolution", TopicEvolutionConfig())
    monkeypatch.setattr(settings, "agent_messages", AgentMessagesConfig())
    yield


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
    """Flask test client wired to `tmp_db`, authenticated as an admin.

    The app gates every /api/ route behind a valid JWT (see
    `web.app._install_auth_gate`), and the session/trace surface is
    additionally admin-only (see `ADMIN_API_ENDPOINTS`), so the default
    client carries an admin token in `environ_base` — this models "a
    logged-in user is browsing" and keeps read/editor-mutation tests
    working. The username stays `test-editor` so identity-derived
    assertions (e.g. bridge sender `web:test-editor`) are unaffected.
    Tests that assert role-specific denials pass their own editor/viewer
    `Authorization` header, which overrides `environ_base` per request;
    tests that assert unauthenticated behaviour (401) use `anon_client`.
    """
    from web.app import create_app
    from lib.auth import create_token
    app = create_app()
    app.config["TESTING"] = True
    token = create_token(1, "test-editor", "admin")
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
