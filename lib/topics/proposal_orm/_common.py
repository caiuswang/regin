"""Shared helpers used by the proposal_orm sub-modules.

Lookup / timestamp helpers + the activity-log accessor — small, no
dependencies on any other proposal_orm submodule.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlmodel import Session, select

from lib.activity_log import get_activity_logger as _get_activity_logger
from lib.orm.models import Repo


def _topics_log():
    return _get_activity_logger("topics")


def _repo_for_path(session: Session, repo_path: str | Path) -> Optional[Repo]:
    p = str(Path(repo_path).resolve())
    return session.exec(select(Repo).where(Repo.path == p)).first()


def _resolve_repo_for_write(repo_path: str | Path) -> Repo:
    """Look up or lazy-upsert a Repo row for `repo_path` — matches
    `apply.resolve_or_create_repo` semantics so tests that go through
    save_proposal without a prior `add-repo` don't crash.
    """
    from lib.topics.snapshots import resolve_or_create_repo
    return resolve_or_create_repo(str(repo_path))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
