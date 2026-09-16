"""Mint, resolve, list and revoke regin API keys (`api_keys`).

Deliberately NOT in `lib/auth.py`: that module owns the *user* principal (JWT
+ password), and an API key is a different one. `lib.auth.require_scope` is the
only auth-side consumer, and it never turns a key into a user session.

The plaintext key is returned exactly once, by `mint_key`. Only its SHA-256 is
stored, so a lost key is re-minted, never recovered — and `resolve_key` fails
closed on every ambiguity: empty input, wrong scheme, unknown hash, revoked row.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import select

from lib.activity_log import get_activity_logger
from lib.orm import SessionLocal
from lib.orm.models.api_keys import ApiKey, KEY_PREFIX_LEN, SCOPES

log = get_activity_logger("tasks")

KEY_SCHEME = "rgn_"
# 16 bytes → 32 hex chars of CSPRNG output. Lookup is a direct index hit on the
# hash of a secret this size, so there is no guessing budget to rate-limit and
# no timing comparison to design around.
_KEY_BYTES = 16


def _now() -> str:
    """UTC, in the same format as the columns' `datetime('now')` default."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _digest(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _load_scopes(raw: Optional[str]) -> list[str]:
    try:
        parsed = json.loads(raw or "[]")
    except (json.JSONDecodeError, TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    return [s for s in parsed if isinstance(s, str)]


def normalize_scopes(scopes) -> list[str]:
    """Keep only scopes this build defines, in order, without duplicates.

    An unrecognized scope is dropped rather than stored: a key must not carry
    a grant nothing enforces, which would read as a capability in the UI."""
    if not isinstance(scopes, (list, tuple)):
        return []
    out: list[str] = []
    for scope in scopes:
        if isinstance(scope, str) and scope in SCOPES and scope not in out:
            out.append(scope)
    return out


def _serialize(key: ApiKey) -> dict:
    return {
        "id": key.id,
        "name": key.name,
        "key_prefix": key.key_prefix,
        "scopes": _load_scopes(key.scopes),
        "created_by": key.created_by,
        "created_at": key.created_at,
        "last_used_at": key.last_used_at,
        "revoked_at": key.revoked_at,
        "revoked": key.revoked_at is not None,
    }


def mint_key(name: str, scopes, created_by: Optional[int] = None) -> tuple[str, dict]:
    """Create a key; return `(plaintext, row)`.

    The plaintext is the caller's only chance to read it — nothing stores it.
    """
    raw = KEY_SCHEME + secrets.token_hex(_KEY_BYTES)
    row = ApiKey(
        name=(name or "").strip()[:200] or "unnamed",
        key_hash=_digest(raw),
        key_prefix=raw[:KEY_PREFIX_LEN],
        scopes=json.dumps(normalize_scopes(scopes)),
        created_by=created_by,
    )
    with SessionLocal() as session:
        session.add(row)
        session.commit()
        session.refresh(row)
        view = _serialize(row)
    log.write("api_key_created", api_key_id=view["id"], name=view["name"],
              scopes=view["scopes"], created_by=created_by)
    return raw, view


def resolve_key(presented: str) -> Optional[dict]:
    """The key row behind a presented plaintext, or None.

    Fails closed on empty/foreign-scheme input, an unknown hash and a revoked
    row. Both sides of the digest compare are guarded explicitly, because
    `compare_digest('', '')` is True and an empty stored hash must never match
    an empty presented key.
    """
    if not presented or not presented.startswith(KEY_SCHEME):
        return None
    digest = _digest(presented)
    with SessionLocal() as session:
        row = session.exec(
            select(ApiKey).where(ApiKey.key_hash == digest)).first()
        if row is None or row.revoked_at is not None:
            return None
        stored = row.key_hash or ""
        if not stored or not hmac.compare_digest(stored, digest):
            return None
        row.last_used_at = _now()
        session.add(row)
        session.commit()
        session.refresh(row)
        view = _serialize(row)
    log.read("api_key_resolved", api_key_id=view["id"], scopes=view["scopes"])
    return view


def list_keys(include_revoked: bool = True) -> list[dict]:
    """Every key, newest first. Never carries key material."""
    with SessionLocal() as session:
        stmt = select(ApiKey).order_by(ApiKey.id.desc())
        if not include_revoked:
            stmt = stmt.where(ApiKey.revoked_at.is_(None))
        return [_serialize(k) for k in session.exec(stmt).all()]


def revoke_key(key_id: int) -> bool:
    """Stamp `revoked_at`. False if the key is unknown or already revoked."""
    with SessionLocal() as session:
        row = session.get(ApiKey, key_id)
        if row is None or row.revoked_at is not None:
            log.warn("api_key_revoke_rejected", api_key_id=key_id,
                     reason="unknown_key" if row is None else "already_revoked")
            return False
        row.revoked_at = _now()
        session.add(row)
        session.commit()
    log.write("api_key_revoked", api_key_id=key_id)
    return True
