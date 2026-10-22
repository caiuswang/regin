"""Lookups every tool module of the `regin` MCP server shares.

Each tool raises `ValueError` with a sentence naming the field or the reason;
FastMCP turns any exception a tool raises into the `ToolError` the client
reads, so a refusal is never a silent empty result.

Regin imports stay inside the functions: `server.py` runs as a script, and a
module-scope `lib.*` import here would load the ORM before a tool is listed.
"""

from __future__ import annotations

from typing import Optional


def project_id(name: Optional[str]) -> Optional[int]:
    if not name:
        return None
    from sqlmodel import select
    from lib.orm import SessionLocal
    from lib.orm.models.sync import Repo
    with SessionLocal() as session:
        rows = session.exec(select(Repo).where(Repo.is_active == 1)).all()
    match = next((r for r in rows if r.name == name), None)
    if match is None:
        known = ", ".join(sorted(r.name for r in rows)) or "none registered"
        raise ValueError(f"no project named {name!r}. Registered: {known}")
    return match.id


def feature(feature_id: int):
    from lib.features import store
    row = store.get(feature_id)
    if row is None:
        raise ValueError(f"no feature {feature_id}")
    return row


def task(task_id: int) -> dict:
    from lib.tasks import store
    found = store.get_task(task_id)
    if found is None:
        raise ValueError(f"no task #{task_id}")
    return found


def defect(defect_id: int):
    from lib.defects import store
    try:
        return store.get(defect_id)
    except store.DefectError:
        raise ValueError(f"no defect {defect_id}") from None


def refused(exc: Exception, field: Optional[str] = None) -> ValueError:
    """A store refusal, re-raised with the field it names when it names one."""
    named = field or getattr(exc, "field", None)
    remedy = getattr(exc, "remedy", None)
    text = f"{named}: {exc}" if named else str(exc)
    return ValueError(f"{text} (remedy: {remedy})" if remedy else text)
