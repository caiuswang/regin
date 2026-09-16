"""Put a cached issue in place without a tracker round trip.

The snapshot moved out of `agent_task_links` and into `issue_cache`, keyed by
the feature rather than by a link row, so every test that used to construct a
link carrying its own snapshot writes here instead.
"""

from __future__ import annotations

from sqlmodel import text

from lib.orm import SessionLocal


def seed(provider: str, key: str, snapshot_json: str,
         synced_at: str = "2026-01-01 00:00:00") -> None:
    with SessionLocal() as session:
        session.exec(text(
            "INSERT OR REPLACE INTO issue_cache (provider, external_key,"
            " snapshot_json, synced_at) VALUES (:p, :k, :s, :t)"
        ).bindparams(p=provider, k=key, s=snapshot_json, t=synced_at))
        session.commit()


def cached(provider: str, key: str):
    """`(snapshot_json, synced_at)` as stored, or `(None, None)`."""
    with SessionLocal() as session:
        row = session.exec(text(
            "SELECT snapshot_json, synced_at FROM issue_cache"
            " WHERE provider = :p AND external_key = :k"
        ).bindparams(p=provider, k=key)).first()
    return (row[0], row[1]) if row else (None, None)
