"""`lib.settings._main_worktree` — the anchor that keeps regin's per-project
data stores (trace DB, agent-memory DB, exported memory tree) on the MAIN
checkout when the code runs from a linked git worktree.

Before this existed, a worktree that ran its own `cli/regin.py` resolved
`db/regin.db` and `db/regin_memory.db` under itself, created them empty, and
then reported the emptiness as evidence: recall returned nothing and
`regin gate recall-ran` counted 0 spans, which the goal-verified-treenav skill
read as "you skipped the recall step".

Every failure path here must degrade to the input path — that is exactly the
old behaviour. Resolving to the *wrong* main would be much worse than not
resolving one, which is why the submodule case below is load-bearing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.settings import _main_worktree


def _worktree(tmp_path: Path, gitdir_text: str) -> Path:
    """A checkout whose `.git` is a file holding `gitdir_text`."""
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".git").write_text(gitdir_text)
    return wt


def test_real_git_directory_resolves_to_itself():
    """A main checkout has a `.git` DIRECTORY; nothing to redirect."""
    root = Path(__file__).resolve().parents[2]
    assert (root / ".git").is_dir()
    assert _main_worktree(root) == root


def test_linked_worktree_resolves_to_the_main_checkout(tmp_path):
    main = tmp_path / "main"
    (main / ".git" / "worktrees" / "feature").mkdir(parents=True)
    wt = _worktree(tmp_path, f"gitdir: {main}/.git/worktrees/feature\n")
    assert _main_worktree(wt) == main


def test_submodule_is_not_mistaken_for_a_worktree(tmp_path):
    """A submodule's `.git` is ALSO a file, but its gitdir sits under
    `.git/modules/`. Following it would silently point a submodule's data
    stores at its superproject's."""
    super_repo = tmp_path / "super"
    (super_repo / ".git" / "modules" / "vendor").mkdir(parents=True)
    sub = _worktree(tmp_path, f"gitdir: {super_repo}/.git/modules/vendor\n")
    assert _main_worktree(sub) == sub


def test_relative_gitdir_is_resolved_against_the_worktree(tmp_path):
    """`git worktree add --relative-paths` (git >= 2.48) writes a relative
    gitdir."""
    main = tmp_path / "main"
    (main / ".git" / "worktrees" / "feature").mkdir(parents=True)
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".git").write_text("gitdir: ../main/.git/worktrees/feature\n")
    assert _main_worktree(wt) == main.resolve()


def test_missing_git_marker_resolves_to_itself(tmp_path):
    """A tarball install / Docker COPY with no git at all."""
    plain = tmp_path / "plain"
    plain.mkdir()
    assert _main_worktree(plain) == plain


@pytest.mark.parametrize("text", [
    "",
    "garbage",
    "gitdir:",
    "not-a-gitdir: /somewhere/.git/worktrees/x",
    "gitdir: /somewhere/else",
    "gitdir: /somewhere/.git/notworktrees/x",
])
def test_malformed_marker_resolves_to_itself(tmp_path, text):
    assert _main_worktree(_worktree(tmp_path, text)) == tmp_path / "wt"


def test_unreadable_marker_resolves_to_itself(tmp_path):
    """A `.git` that is a DIRECTORY we cannot stat, or any other OSError,
    must not raise out of `Settings` construction."""
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".git").mkdir()  # is_file() is False → degrade
    assert _main_worktree(wt) == wt


# ── the paths that actually consume the anchor ───────────────────────────

def test_main_checkout_is_a_no_op(monkeypatch):
    """In a normal checkout the anchor changes nothing — the invariant that
    makes this change safe to land."""
    from lib.settings import settings
    assert settings.main_worktree == settings.project_root


def test_memory_db_follows_the_main_worktree(monkeypatch, tmp_path):
    from lib.settings import AgentMemoryConfig, settings
    from lib.memory.engine import memory_db_path

    fresh = AgentMemoryConfig()
    fresh.db_path = None  # fall through to the project default
    monkeypatch.setattr(settings, "agent_memory", fresh)
    monkeypatch.setattr(settings, "project_root", tmp_path / "wt")
    monkeypatch.setattr(settings, "main_worktree", tmp_path / "main")

    assert memory_db_path() == str(tmp_path / "main" / "db" / "regin_memory.db")


def test_explicit_memory_db_path_still_wins(monkeypatch, tmp_path):
    """The `tmp_memory_db` fixture isolates via an explicit `db_path`; the
    anchor must not reach that far."""
    from lib.settings import AgentMemoryConfig, settings
    from lib.memory.engine import memory_db_path

    fresh = AgentMemoryConfig()
    fresh.db_path = tmp_path / "explicit.db"
    monkeypatch.setattr(settings, "agent_memory", fresh)
    monkeypatch.setattr(settings, "main_worktree", tmp_path / "main")

    assert memory_db_path() == str(tmp_path / "explicit.db")


def test_tmp_db_fixture_still_isolates_the_trace_db(tmp_path):
    """Autouse `tmp_db` patches `lib.orm.engine.DB_PATH` directly, so the
    anchor at its definition site must not be re-read by any caller. If this
    fails, the whole suite is writing to the developer's real trace DB."""
    import lib.orm.engine as engine
    assert "test.db" in engine.DB_PATH
    assert str(tmp_path) in engine.DB_PATH


def test_schema_path_stays_on_this_checkout():
    """schema.sql and alembic/versions/ are branch source: a feature branch
    must not apply its migrations to the shared DB by accident."""
    import lib.orm.engine as engine
    from lib.settings import settings
    assert str(settings.project_root) in engine.SCHEMA_PATH
