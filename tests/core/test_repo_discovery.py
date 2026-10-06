"""Unit tests for lib.sync.repo_discovery under the explicit repo-paths model.

Each entry in ``settings.repo_paths`` is the absolute path of a git
repo. ``scan_repos`` resolves the list into descriptors (name, path,
default_branch); ``register_repos`` reconciles the ``repos`` /
``branches`` tables; ``add_repo`` / ``remove_repo`` mutate both the
settings file and the DB.

Uses ``tmp_db`` so each test starts with empty repos/branches tables.
"""

from __future__ import annotations

import os
import subprocess

import pytest
from sqlmodel import select

from lib.orm import SessionLocal
from lib.orm.models import Branch, Repo
from lib.sync.repo_discovery import (
    RepoAddError,
    add_repo,
    detect_default_branch,
    register_repos,
    remove_repo,
    scan_repos,
)


def _fetch_repos():
    with SessionLocal() as s:
        return s.exec(select(Repo)).all()


def _fetch_branches(repo_id):
    with SessionLocal() as s:
        return s.exec(select(Branch).where(Branch.repo_id == repo_id)).all()


def _make_git_repo(path):
    """Initialize a bare-bones git repo so is_git_repo / get_branches work."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "--allow-empty", "-m", "init", "-q"],
        check=True,
        env={**os.environ,
             "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"},
    )


# ── register_repos ───────────────────────────────────────────

def test_register_repos_adds_new(tmp_db):
    stats = register_repos([{"name": "alpha", "path": "/tmp/alpha", "default_branch": "main"}])
    assert stats == {"added": 1, "updated": 0, "skipped": 0, "removed": 0}
    assert [r.name for r in _fetch_repos()] == ["alpha"]


def test_register_repos_skips_unchanged(tmp_db):
    repos = [{"name": "alpha", "path": "/tmp/alpha", "default_branch": "main"}]
    register_repos(repos)
    stats = register_repos(repos)
    assert stats["added"] == 0
    assert stats["skipped"] == 1


def test_register_repos_updates_changed_path(tmp_db):
    register_repos([{"name": "alpha", "path": "/old", "default_branch": "main"}])
    stats = register_repos([{"name": "alpha", "path": "/new", "default_branch": "main"}])
    assert stats["updated"] == 1
    assert _fetch_repos()[0].path == "/new"


def test_register_repos_removes_missing(tmp_db):
    register_repos([
        {"name": "alpha", "path": "/a", "default_branch": "main"},
        {"name": "beta", "path": "/b", "default_branch": "main"},
    ])
    stats = register_repos([
        {"name": "alpha", "path": "/a", "default_branch": "main"},
    ])
    assert stats["removed"] == 1
    assert {r.name for r in _fetch_repos()} == {"alpha"}


def test_register_repos_cascades_branch_delete(tmp_db):
    register_repos([{"name": "a", "path": "/a", "default_branch": "main"}])
    register_repos([])
    assert _fetch_repos() == []
    with SessionLocal() as s:
        assert s.exec(select(Branch)).all() == []


# ── detect_default_branch ────────────────────────────────────

def test_detect_default_branch_prefers_main(monkeypatch):
    from lib.sync import repo_discovery
    monkeypatch.setattr(repo_discovery, "get_branches",
                        lambda _p: ["dev", "master", "main"])
    assert detect_default_branch("/r") == "main"


def test_detect_default_branch_falls_back_to_first(monkeypatch):
    from lib.sync import repo_discovery
    monkeypatch.setattr(repo_discovery, "get_branches",
                        lambda _p: ["develop", "feature-x"])
    assert detect_default_branch("/r") == "develop"


# ── scan_repos (explicit repo_paths) ─────────────────────────

def test_scan_repos_resolves_each_repo_path(tmp_db, tmp_path, monkeypatch):
    """Each entry in repo_paths is a git working tree; scan_repos
    enriches it with name + default branch."""
    from lib.sync import repo_discovery
    alpha = tmp_path / "alpha"
    beta = tmp_path / "beta"
    _make_git_repo(alpha)
    _make_git_repo(beta)

    monkeypatch.setattr(repo_discovery, "_load_repo_paths",
                        lambda: [str(alpha), str(beta)])
    out = scan_repos()
    names = {r["name"] for r in out}
    assert names == {"alpha", "beta"}
    assert all(r["default_branch"] == "main" for r in out)


def test_scan_repos_drops_non_git_or_missing_entries(tmp_db, tmp_path, monkeypatch):
    """Entries that don't exist or aren't git repos are silently
    skipped — they'd fail the is_git_repo guard anyway."""
    from lib.sync import repo_discovery
    real = tmp_path / "real"
    _make_git_repo(real)
    missing = tmp_path / "missing"
    not_git = tmp_path / "not_git"
    not_git.mkdir()

    monkeypatch.setattr(repo_discovery, "_load_repo_paths",
                        lambda: [str(real), str(missing), str(not_git)])
    out = scan_repos()
    assert [r["name"] for r in out] == ["real"]


# ── add_repo ─────────────────────────────────────────────────

@pytest.fixture
def _isolated_settings(tmp_path, monkeypatch):
    """Redirect settings file paths so add/remove writes go to tmp."""
    shared = tmp_path / "settings.json"
    local = tmp_path / "settings.local.json"
    from lib import settings as _cfg
    monkeypatch.setattr(_cfg, "SETTINGS_PATH", str(shared))
    monkeypatch.setattr(_cfg, "SETTINGS_LOCAL_PATH", str(local))
    monkeypatch.setattr(_cfg, "CONFIG_DIR", str(tmp_path))
    return {"shared": shared, "local": local}


def test_add_repo_rejects_non_existent_path(tmp_db, tmp_path, _isolated_settings):
    with pytest.raises(RepoAddError, match="does not exist"):
        add_repo(str(tmp_path / "nope"))


def test_add_repo_rejects_non_git_dir(tmp_db, tmp_path, _isolated_settings):
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(RepoAddError, match="not a git"):
        add_repo(str(plain))


def test_add_repo_persists_and_registers(tmp_db, tmp_path, _isolated_settings):
    repo_path = tmp_path / "svc"
    _make_git_repo(repo_path)
    info = add_repo(str(repo_path))
    assert info["name"] == "svc"
    assert info["default_branch"] == "main"
    assert [r.name for r in _fetch_repos()] == ["svc"]


def test_add_repo_rejects_duplicate(tmp_db, tmp_path, _isolated_settings):
    repo_path = tmp_path / "svc"
    _make_git_repo(repo_path)
    add_repo(str(repo_path))
    with pytest.raises(RepoAddError, match="already registered"):
        add_repo(str(repo_path))


def test_concurrent_add_repo_keeps_both_rows(tmp_db, tmp_path, _isolated_settings,
                                             monkeypatch):
    """Two registrations interleaved inside one process (threaded Flask) must
    not lose an update: each one loads `repo_paths` and saves it back, so an
    unserialized pair drops whichever path the other wrote."""
    import threading
    from contextlib import contextmanager

    import lib.sync.repo_discovery as rd

    first, second = tmp_path / "first", tmp_path / "second"
    _make_git_repo(first)
    _make_git_repo(second)

    # Set by the second registration once it has either read the paths
    # (unserialized: the lost update is now certain) or reached the lock
    # (serialized: it cannot read them until the first is done).
    second_progressed = threading.Event()
    real_load, real_save = rd._load_repo_paths, rd._save_repo_paths
    real_locked = rd.settings_file_lock

    @contextmanager
    def signalling_locked():
        if threading.current_thread().name == "second":
            second_progressed.set()
        with real_locked():
            yield

    def load():
        paths = real_load()
        if threading.current_thread().name == "second":
            second_progressed.set()
        return paths

    def save(paths):
        if threading.current_thread().name == "first":
            second_progressed.wait()
        real_save(paths)

    monkeypatch.setattr(rd, "_load_repo_paths", load)
    monkeypatch.setattr(rd, "_save_repo_paths", save)
    monkeypatch.setattr(rd, "settings_file_lock", signalling_locked)

    t1 = threading.Thread(target=add_repo, args=(str(first),), name="first")
    t2 = threading.Thread(target=add_repo, args=(str(second),), name="second")
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert sorted(r.name for r in _fetch_repos()) == ["first", "second"]
    assert sorted(os.path.basename(p) for p in rd._load_repo_paths()) == [
        "first", "second"]


def test_add_repo_keeps_rows_it_did_not_list(tmp_db, tmp_path, _isolated_settings):
    register_repos([{"name": "seeded", "path": "/elsewhere/seeded",
                     "default_branch": "main"}])
    repo_path = tmp_path / "svc"
    _make_git_repo(repo_path)
    add_repo(str(repo_path))
    assert sorted(r.name for r in _fetch_repos()) == ["seeded", "svc"]


def test_add_repo_waits_for_a_settings_writer_in_another_process(
        tmp_db, tmp_path, _isolated_settings, monkeypatch):
    """A CLI `regin add-repo` and the server are separate processes, so an
    in-process lock cannot serialize them. The writer here holds the settings
    lock through its own file descriptor, which is what another process has."""
    import fcntl
    import json
    import threading

    import lib.sync.repo_discovery as rd

    theirs, ours = tmp_path / "theirs", tmp_path / "ours"
    _make_git_repo(theirs)
    _make_git_repo(ours)

    reached = threading.Event()
    real_flock = fcntl.flock
    real_load = rd._load_repo_paths

    def flock(fh, op):
        if threading.current_thread().name == "ours" and op == fcntl.LOCK_EX:
            reached.set()
        return real_flock(fh, op)

    def load():
        if threading.current_thread().name == "ours":
            reached.set()
        return real_load()

    monkeypatch.setattr(fcntl, "flock", flock)
    monkeypatch.setattr(rd, "_load_repo_paths", load)

    lock_path = str(_isolated_settings["local"]) + ".lock"
    with open(lock_path, "a") as other_process:
        real_flock(other_process, fcntl.LOCK_EX)
        t = threading.Thread(target=add_repo, args=(str(ours),), name="ours")
        t.start()
        reached.wait()
        _isolated_settings["local"].write_text(json.dumps(
            {"repo_paths": [os.path.realpath(theirs)]}))
        real_flock(other_process, fcntl.LOCK_UN)
    t.join()

    assert sorted(os.path.basename(p) for p in real_load()) == ["ours", "theirs"]


# ── remove_repo ──────────────────────────────────────────────

def test_remove_repo_drops_db_rows(tmp_db, tmp_path, _isolated_settings):
    repo_path = tmp_path / "svc"
    _make_git_repo(repo_path)
    add_repo(str(repo_path))
    result = remove_repo("svc")
    assert result["removed"] is True
    assert _fetch_repos() == []


def test_remove_repo_not_found(tmp_db, _isolated_settings):
    result = remove_repo("never-added")
    assert result["removed"] is False


def test_remove_repo_cascades_to_session_repos(tmp_db, tmp_path, _isolated_settings):
    """session_repos has no FK cascade, so remove_repo must drop its tags
    manually — otherwise removing a repo orphans session→repo links."""
    import sqlite3
    import lib.orm.engine as db_module
    repo_path = tmp_path / "svc"
    _make_git_repo(repo_path)
    add_repo(str(repo_path))
    with SessionLocal() as s:
        rid = s.exec(select(Repo).where(Repo.name == "svc")).first().id

    conn = sqlite3.connect(str(db_module.DB_PATH))
    try:
        conn.execute("INSERT INTO sessions (trace_id, started_at, last_seen) "
                     "VALUES ('t-cascade', '2026-01-01', '2026-01-01')")
        conn.execute("INSERT INTO session_repos (trace_id, repo_id, is_primary) "
                     "VALUES ('t-cascade', ?, 1)", (rid,))
        conn.commit()
    finally:
        conn.close()

    remove_repo("svc")

    conn = sqlite3.connect(str(db_module.DB_PATH))
    try:
        remaining = conn.execute(
            "SELECT COUNT(*) FROM session_repos WHERE repo_id = ?", (rid,)
        ).fetchone()[0]
    finally:
        conn.close()
    assert remaining == 0


# ── prune_orphan_repos ───────────────────────────────────────

def test_prune_orphan_repos_drops_missing_and_tmpdir(tmp_db, tmp_path, monkeypatch, _isolated_settings):
    """Rows whose path is gone or lives under `$TMPDIR` are removed;
    rows pointing at a real repo outside TMPDIR are kept."""
    from lib.sync.repo_discovery import prune_orphan_repos

    # tmp_path is under the OS tempdir on macOS, so anything we create
    # there counts as "under_tmpdir". Point gettempdir at a sibling so
    # we can also exercise the missing_path branch without spuriously
    # tripping the tmpdir branch first.
    fake_tmp = tmp_path / "fake_tmp"
    fake_tmp.mkdir()
    monkeypatch.setattr("lib.sync.repo_discovery.tempfile.gettempdir",
                        lambda: str(fake_tmp))

    real = tmp_path / "real"  # outside fake_tmp — should survive
    _make_git_repo(real)
    under_tmp = fake_tmp / "scratch"
    _make_git_repo(under_tmp)
    missing = tmp_path / "gone"  # never created — orphan by missing path

    register_repos([
        {"name": "real", "path": str(real), "default_branch": "main"},
        {"name": "under_tmp", "path": str(under_tmp), "default_branch": "main"},
        {"name": "missing", "path": str(missing), "default_branch": "main"},
    ])

    pruned = prune_orphan_repos()
    assert {row["name"] for row in pruned} == {"under_tmp", "missing"}
    reasons = {row["name"]: row["reason"] for row in pruned}
    assert reasons == {"under_tmp": "under_tmpdir", "missing": "missing_path"}
    assert [r.name for r in _fetch_repos()] == ["real"]


def test_prune_orphan_repos_dry_run_does_not_delete(tmp_db, tmp_path, monkeypatch, _isolated_settings):
    from lib.sync.repo_discovery import prune_orphan_repos

    monkeypatch.setattr("lib.sync.repo_discovery.tempfile.gettempdir",
                        lambda: str(tmp_path))
    under_tmp = tmp_path / "scratch"
    _make_git_repo(under_tmp)
    register_repos([{"name": "scratch", "path": str(under_tmp),
                     "default_branch": "main"}])

    pruned = prune_orphan_repos(dry_run=True)
    assert [row["name"] for row in pruned] == ["scratch"]
    assert [r.name for r in _fetch_repos()] == ["scratch"]


def test_prune_orphan_repos_keeps_registered_tmp_paths(tmp_db, tmp_path, monkeypatch, _isolated_settings):
    """If a user has explicitly registered a path under /tmp it's not
    an orphan — `settings.repo_paths` is the source of truth."""
    from lib.sync.repo_discovery import prune_orphan_repos

    monkeypatch.setattr("lib.sync.repo_discovery.tempfile.gettempdir",
                        lambda: str(tmp_path))
    under_tmp = tmp_path / "scratch"
    _make_git_repo(under_tmp)
    add_repo(str(under_tmp))  # adds to settings.repo_paths

    pruned = prune_orphan_repos()
    assert pruned == []
    assert [r.name for r in _fetch_repos()] == ["scratch"]
