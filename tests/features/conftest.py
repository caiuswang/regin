"""Shared feature-ledger fixtures (see `tests/feature_ledger_fixtures.py`)."""

import pytest

from tests.tasks.conftest import native_task, task_run_row  # noqa: F401
from tests.feature_ledger_fixtures import (  # noqa: F401
    attributing_trace, counted_queries, defect_row, feature_row, no_tracker,
    posted_spans, run_alias, session_row, task_lane, tracker_down,
    tracker_issue,
)


@pytest.fixture
def install_db_copy(tmp_path):
    """A backup of this install's trace DB, safe to migrate.

    Skips when there is no install DB, and when the install is stamped at a
    revision this checkout lacks: that install was migrated by work in another
    worktree that has not landed, so no `migrate` from here can resolve it and
    it says nothing about this checkout's schema.
    """
    import sqlite3
    from pathlib import Path

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from lib.settings import settings

    install_db = Path(settings.main_worktree) / "db" / "regin.db"
    if not install_db.is_file():
        pytest.skip(f"no install DB at {install_db} — `regin init` makes one")
    copy = tmp_path / "regin.db"
    source = sqlite3.connect(f"file:{install_db}?mode=ro", uri=True)
    target = sqlite3.connect(copy)
    try:
        source.backup(target)
        try:
            row = target.execute("SELECT version_num FROM alembic_version").fetchone()
        except sqlite3.OperationalError:
            row = None
    finally:
        target.close()
        source.close()

    repo_root = Path(__file__).resolve().parent.parent.parent
    script = ScriptDirectory.from_config(Config(str(repo_root / "alembic.ini")))
    known = {rev.revision for rev in script.walk_revisions()}
    if row and row[0] not in known:
        pytest.skip(f"install DB is stamped {row[0]!r}, a revision this checkout "
                    "does not have — migrated by work that has not landed")
    return copy
