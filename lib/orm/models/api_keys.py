"""Machine credentials for the non-JWT API surface (`api_keys`).

An API key is a DIFFERENT principal from a user, not a weaker one. Nothing on
this path populates `g._current_user`, so a key cannot satisfy `require_auth`,
`require_editor` or `require_role` on any route it reaches — see
`lib.auth.require_scope` and `lib/api_keys.py`.

`key_hash` (SHA-256 of the full `rgn_<32 hex>` key) is the only lookup path:
the plaintext is shown once at creation and never stored, so a lost key is
re-minted rather than recovered. `key_prefix` exists so a row can be named in
the UI without carrying the secret.
"""

from __future__ import annotations

from typing import Optional

from sqlmodel import Column, Field, Integer, String, Text
from sqlalchemy import text

from lib.orm.base import Base

# Displayed stem of the key ("rgn_a1b2…") — the 4-char scheme tag plus four
# hex chars, which is short enough to be non-reversible and long enough to
# tell two of a user's keys apart in a list.
KEY_PREFIX_LEN = 8

SCOPE_TASK_CAPTURE = "task:capture"
SCOPES: tuple[str, ...] = (SCOPE_TASK_CAPTURE,)


class ApiKey(Base, table=True):
    """One minted API key. `revoked_at` non-null ⇒ the key is refused."""

    __tablename__ = "api_keys"
    __table_args__ = {"sqlite_autoincrement": True}

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(sa_column=Column("name", String, nullable=False))
    key_hash: str = Field(
        sa_column=Column("key_hash", String, nullable=False, unique=True))
    key_prefix: str = Field(
        sa_column=Column("key_prefix", String, nullable=False))
    scopes: str = Field(
        default="[]",
        sa_column=Column("scopes", Text, nullable=False,
                         server_default=text("'[]'")))
    # A provenance label, not an enforced join: `users` lives in a separate
    # database in shared mode (`AuthSessionLocal`), where a foreign key to it
    # is not expressible.
    created_by: Optional[int] = Field(
        default=None, sa_column=Column("created_by", Integer))
    created_at: str = Field(
        sa_column=Column("created_at", String, nullable=False,
                         server_default=text("(datetime('now'))")))
    last_used_at: Optional[str] = Field(
        default=None, sa_column=Column("last_used_at", String))
    revoked_at: Optional[str] = Field(
        default=None, sa_column=Column("revoked_at", String))
