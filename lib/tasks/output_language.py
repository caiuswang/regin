"""The language a task run is told to write in: a project's own, else the
global default, else nothing at all.
"""

from __future__ import annotations

from typing import Optional

from lib.activity_log import get_activity_logger
from lib.orm import SessionLocal
from lib.orm.models.sync import Repo
from lib.settings import settings

log = get_activity_logger("tasks")

LANGUAGE_MAX = 64

SOURCE_PROJECT = "project"
SOURCE_DEFAULT = "default"
SOURCE_NONE = "none"


class LanguageError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status
        self.field = "output_language"


def project_language(project_id: Optional[int]) -> str:
    if project_id is None:
        return ""
    with SessionLocal() as session:
        repo = session.get(Repo, project_id)
        return (repo.output_language or "").strip() if repo else ""


def resolve(project_id: Optional[int]) -> tuple[str, str]:
    """(language, source) for a run filed under `project_id`."""
    own = project_language(project_id)
    if own:
        return own, SOURCE_PROJECT
    default = (settings.tasks.output_language or "").strip()
    if default:
        return default, SOURCE_DEFAULT
    return "", SOURCE_NONE


def set_project_language(project_id: int, value) -> Optional[str]:
    """Store a project's language; blank clears it to NULL."""
    if value is not None and not isinstance(value, str):
        raise LanguageError("output_language must be a string")
    cleaned = (value or "").strip()
    if len(cleaned) > LANGUAGE_MAX:
        raise LanguageError(
            f"output_language is at most {LANGUAGE_MAX} characters")
    with SessionLocal() as session:
        repo = session.get(Repo, project_id)
        if repo is None:
            raise LanguageError(f"unknown project: {project_id}", status=404)
        repo.output_language = cleaned or None
        session.add(repo)
        session.commit()
    log.write("project_output_language_set", project_id=project_id,
              language=cleaned)
    return cleaned or None
