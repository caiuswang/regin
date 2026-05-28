"""Unit tests for lib.utils.pagination.

Covers cursor encoding/decoding, clamp helpers, and the two
SQLModel-native page helpers (keyset_page_stmt, paginate_query_stmt)
added during Phase B.4.9 / B.5.3. The legacy raw-SQL helper
`keyset_page` is exercised indirectly by trace_service tests.
"""

from __future__ import annotations

import pytest
from sqlmodel import select

from lib.utils.pagination import (
    MAX_PAGE_SIZE, clamp_page, clamp_size,
    decode_cursor, encode_cursor,
    keyset_page_stmt, paginate_query_stmt,
)


# ── clamp helpers ────────────────────────────────────────────

def test_clamp_size_defaults_to_default_when_none():
    assert clamp_size(None) == 50  # DEFAULT_PAGE_SIZE


def test_clamp_size_honors_explicit_default():
    assert clamp_size(None, default=10) == 10


def test_clamp_size_caps_at_max():
    assert clamp_size(10_000) == MAX_PAGE_SIZE


def test_clamp_size_rejects_zero_and_negative():
    assert clamp_size(0) == 50  # falls back to default
    assert clamp_size(-5) == 50


def test_clamp_size_parses_strings():
    assert clamp_size("25") == 25


def test_clamp_size_bogus_input_falls_back():
    assert clamp_size("nope") == 50
    assert clamp_size([]) == 50


def test_clamp_page_nonnegative():
    assert clamp_page(None) == 0
    assert clamp_page(-3) == 0
    assert clamp_page("7") == 7
    assert clamp_page("bogus") == 0


# ── cursor codec ─────────────────────────────────────────────

def test_cursor_round_trip():
    token = encode_cursor(["2026-04-21T10:00:00", 42])
    assert decode_cursor(token) == ["2026-04-21T10:00:00", 42]


def test_cursor_handles_none_token():
    assert decode_cursor(None) is None


def test_cursor_handles_garbage():
    assert decode_cursor("not!base64$$") is None
    assert decode_cursor("") is None


# ── keyset_page_stmt ─────────────────────────────────────────

def test_keyset_page_stmt_returns_first_page(tmp_db):
    from lib.orm import SessionLocal
    from lib.orm.models import RuleTrigger
    with SessionLocal() as s:
        for i in range(5):
            s.add(RuleTrigger(
                rule_id=f"rule-{i}", file_path=f"/x/{i}",
                match_count=0, triggered=0,
                checked_at=f"2026-04-21T10:00:{i:02d}",
            ))
        s.commit()
    with SessionLocal() as s:
        stmt = select(RuleTrigger)
        page = keyset_page_stmt(
            s, stmt,
            order_cols=[(RuleTrigger.checked_at, "DESC"), (RuleTrigger.id, "DESC")],
            cursor_token=None, size=3,
        )
    assert len(page.items) == 3
    assert page.next_cursor is not None


def test_keyset_page_stmt_pagination_is_disjoint(tmp_db):
    from lib.orm import SessionLocal
    from lib.orm.models import RuleTrigger
    with SessionLocal() as s:
        for i in range(5):
            s.add(RuleTrigger(
                rule_id=f"r{i}", file_path="/p", match_count=0,
                triggered=0, checked_at=f"2026-04-21T10:00:{i:02d}",
            ))
        s.commit()
    with SessionLocal() as s:
        stmt = select(RuleTrigger)
        p1 = keyset_page_stmt(
            s, stmt,
            order_cols=[(RuleTrigger.checked_at, "DESC"), (RuleTrigger.id, "DESC")],
            cursor_token=None, size=2,
        )
        p2 = keyset_page_stmt(
            s, stmt,
            order_cols=[(RuleTrigger.checked_at, "DESC"), (RuleTrigger.id, "DESC")],
            cursor_token=p1.next_cursor, size=2,
        )
    ids1 = {r["id"] for r in p1.items}
    ids2 = {r["id"] for r in p2.items}
    assert ids1.isdisjoint(ids2)


def test_keyset_page_stmt_terminal_page_has_no_cursor(tmp_db):
    from lib.orm import SessionLocal
    from lib.orm.models import RuleTrigger
    with SessionLocal() as s:
        s.add(RuleTrigger(
            rule_id="solo", file_path="/x", match_count=0, triggered=0,
            checked_at="2026-04-21T10:00:00",
        ))
        s.commit()
    with SessionLocal() as s:
        stmt = select(RuleTrigger)
        page = keyset_page_stmt(
            s, stmt,
            order_cols=[(RuleTrigger.checked_at, "DESC"), (RuleTrigger.id, "DESC")],
            cursor_token=None, size=10,
        )
    assert len(page.items) == 1
    assert page.next_cursor is None


# ── paginate_query_stmt ──────────────────────────────────────

def test_paginate_query_stmt_counts_total(tmp_db):
    from lib.orm import SessionLocal
    from lib.orm.models import Repo
    with SessionLocal() as s:
        for i in range(7):
            s.add(Repo(name=f"r{i}", path=f"/r{i}", default_branch="main"))
        s.commit()
    with SessionLocal() as s:
        stmt = select(Repo).order_by(Repo.id)
        page = paginate_query_stmt(s, stmt, page=0, size=3)
    assert page.total == 7
    assert len(page.items) == 3
    assert page.has_next is True
    assert page.has_prev is False


def test_paginate_query_stmt_last_page_flags(tmp_db):
    from lib.orm import SessionLocal
    from lib.orm.models import Repo
    with SessionLocal() as s:
        for i in range(3):
            s.add(Repo(name=f"r{i}", path=f"/r{i}", default_branch="main"))
        s.commit()
    with SessionLocal() as s:
        stmt = select(Repo).order_by(Repo.id)
        # Last page of a 3-row / size=2 set.
        page = paginate_query_stmt(s, stmt, page=1, size=2)
    assert page.page == 1
    assert page.has_next is False
    assert page.has_prev is True
    assert len(page.items) == 1


def test_paginate_query_stmt_to_envelope(tmp_db):
    from lib.orm import SessionLocal
    from lib.orm.models import Repo
    with SessionLocal() as s:
        s.add(Repo(name="only", path="/o", default_branch="main"))
        s.commit()
    with SessionLocal() as s:
        page = paginate_query_stmt(s, select(Repo).order_by(Repo.id),
                                   page=0, size=10)
    env = page.to_envelope()
    assert env["pagination"]["strategy"] == "offset"
    assert env["pagination"]["total"] == 1
