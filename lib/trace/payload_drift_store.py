"""Persist DriftFinding rows into `payload_schema_drift` via upsert.

Kept separate from `payload_validation.py` so pure-validation code has
no DB dependency (used by tests, the bootstrap CLI, etc.). The hook
handler calls `record_findings()` on the hot path; it must never raise.
"""

from __future__ import annotations

import hashlib
import json
from typing import Iterable

from sqlalchemy import text as sa_text

from lib.notifications.notify import notify_counts_changed
from lib.orm import SessionLocal
from lib.trace.payload_validation import DriftFinding


def _sha256(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


_UPSERT_SQL = sa_text("""
    INSERT INTO payload_schema_drift (
        agent, subject_kind, tool_name, drift_kind, field_path, expected, sample_value,
        sample_payload_sha, claude_version
    )
    VALUES (
        :agent, :subject_kind, :tool_name, :drift_kind, :field_path, :expected, :sample_value,
        :sample_payload_sha, :claude_version
    )
    ON CONFLICT(agent, subject_kind, tool_name, drift_kind, field_path, claude_version) DO UPDATE SET
        last_seen        = datetime('now'),
        occurrence_count = occurrence_count + 1
""")


def _client_version_for(agent: str) -> str | None:
    """Per-provider CLI version for the drift fingerprint. Dispatches to the
    provider adapter's `client_version()` so a new provider's version probe is
    just an override — no vendor `if` here. (The `claude_version` column keeps
    its legacy name but holds whichever provider's version applies.) Today only
    Claude's probe is wired; others return None until theirs lands."""
    from lib.providers import build_provider, is_provider_id
    if not is_provider_id(agent):
        return None
    return build_provider(agent).client_version()


def pending_drift_count() -> int:
    """Findings awaiting a human decision — the nav badge number."""
    with SessionLocal() as session:
        return session.execute(sa_text(
            "SELECT COUNT(*) FROM payload_schema_drift WHERE status = 'pending'"
        )).scalar() or 0


def record_findings(findings: Iterable[DriftFinding], payload: dict) -> int:
    """Insert or bump each finding. Returns rows written. Never raises."""
    items = list(findings)
    if not items:
        return 0
    sha = _sha256(payload)
    written = 0
    try:
        with SessionLocal() as session:
            for f in items:
                session.execute(_UPSERT_SQL, {
                    "agent": f.agent,
                    "subject_kind": f.subject_kind,
                    "tool_name": f.tool_name,
                    "drift_kind": f.drift_kind,
                    "field_path": f.field_path,
                    "expected": f.expected,
                    "sample_value": f.actual_sample,
                    "sample_payload_sha": sha,
                    "claude_version": _client_version_for(f.agent),
                })
                written += 1
            session.commit()
    except Exception:
        return 0
    if written:
        notify_counts_changed()
    return written
