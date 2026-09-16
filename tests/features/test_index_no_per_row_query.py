"""Invariant 8: no figure on the feature index is produced by a per-row query.

The ledger is the counter-example this exists to keep out. `ledger.build()`
measured 726-1679 ms per feature, so a page that computed its figures a row at
a time would be 15-35 s — and the failure is silent, because a loop that reads
one extra row per feature looks exactly like a correct page until the
population grows.

Counted rather than asserted structurally: a statement counter on the engine is
the only check that survives someone adding a second helper read, where reading
the SQL text for a `for` loop would not. The bar is the shape of the growth —
a page of 20 rows must cost the same number of statements as a page of 2 — not
a magic constant, so the test does not break the day a legitimate CTE is split
in two.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import event

from lib.auth import create_token
from lib.features import index as index_module
from lib.features import store as feature_store
from lib.orm import get_engine


def _headers(role: str = "viewer") -> dict:
    return {"Authorization": f"Bearer {create_token(1, role, role)}"}


@contextmanager
def counted():
    """Every statement the ORM engine executes inside the block."""
    statements: list[str] = []

    def record(_conn, _cursor, statement, *_rest):
        statements.append(statement)

    engine = get_engine()
    event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record)


def _make(count: int) -> list:
    return [feature_store.create(f"feature {n:03d}").id for n in range(count)]


def test_no_list_figure_issues_a_per_row_query(tmp_db):
    """The acceptance item. Ten times the rows, the same number of reads."""
    _make(2)
    with counted() as small:
        index_module.page(page_index=0, size=50)
    small_count = len(small)
    assert small_count > 0, "the statement counter observed nothing at all"

    _make(18)
    with counted() as large:
        rows = index_module.page(page_index=0, size=50)

    assert len(rows.items) == 20
    assert len(large) == small_count, (
        "the index read more statements for 20 rows than for 2 — a figure on "
        f"this page is computed per row:\n  2 rows: {small}\n 20 rows: {large}")


def test_the_http_index_is_flat_too(flask_client, tmp_db):
    """The same bar through the blueprint, because the route is what the
    browser calls and a per-row read added in serialization would not show up
    in `index_module.page` alone."""
    _make(3)
    with counted() as small:
        flask_client.get("/api/features", headers=_headers())

    _make(17)
    with counted() as large:
        flask_client.get("/api/features", headers=_headers())

    assert len(large) == len(small)


@pytest.mark.parametrize("sort", ["activity:desc", "rounds:desc", "cost:asc",
                                  "stage:asc", "open_defects:desc"])
def test_every_sort_stays_flat(tmp_db, sort):
    """A sort is an ORDER BY, never a second read: the figure it orders by is
    already a column of the one statement."""
    _make(2)
    with counted() as small:
        index_module.page(filters={"sort": sort}, page_index=0, size=50)

    _make(18)
    with counted() as large:
        index_module.page(filters={"sort": sort}, page_index=0, size=50)

    assert len(large) == len(small)
