"""Pagination primitives: Page (offset-limit) and CursorPage (keyset).

Every paginated endpoint returns one of these two shapes. Both serialize to
the same top-level JSON envelope — ``{ items, pagination: {...} }`` — so the
frontend composables (`usePage`, `useCursor`) only need to care about which
strategy is in use, not about per-endpoint variance.

Rationale for the split:

* **Offset** is simple, supports "go to page N" UI, but re-reads every row
  up to the offset on each request. Fine for slow-growth, user-initiated
  browsing (audit log, history) where pages are rarely deeper than a few
  hundred rows and concurrent inserts are rare.
* **Keyset** (cursor) is O(log n) regardless of depth and immune to
  concurrent inserts/deletes shifting pages, but can only express
  prev/next + "jump to top". Right choice for the hot ingest tables
  (``rule_triggers``, ``skill_reads``, ``session_spans``, ``sessions``)
  where rows arrive continuously while a user is browsing.

One module, one contract: endpoints build the SQL + params, call the
helper, and return the resulting object via ``asdict``.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any

# Hard cap shared by both strategies. Keeps a rogue client from asking for
# 10 000 rows at once even when the endpoint's default is smaller.
MAX_PAGE_SIZE = 200
DEFAULT_PAGE_SIZE = 50


def clamp_size(raw, default: int = DEFAULT_PAGE_SIZE) -> int:
    """Parse + clamp the requested size. Bogus input falls back to default."""
    try:
        size = int(raw) if raw is not None else default
    except (TypeError, ValueError):
        return default
    if size <= 0:
        return default
    return min(size, MAX_PAGE_SIZE)


def clamp_page(raw) -> int:
    """Parse a 0-based page index; bogus input → 0."""
    try:
        page = int(raw) if raw is not None else 0
    except (TypeError, ValueError):
        return 0
    return max(0, page)


# ── Offset-limit ──────────────────────────────────────────────

@dataclass
class Page:
    items: list[Any]
    total: int          # COUNT(*) of the filtered query
    page: int           # 0-based
    size: int
    has_next: bool
    has_prev: bool

    def to_envelope(self) -> dict:
        """Frontend-facing JSON shape."""
        return {
            'items': self.items,
            'pagination': {
                'strategy': 'offset',
                'total': self.total,
                'page': self.page,
                'size': self.size,
                'has_next': self.has_next,
                'has_prev': self.has_prev,
            },
        }


def paginate_query_stmt(session, stmt, page: int, size: int,
                        row_transform=None, row_to_dict=None) -> Page:
    """Offset-limit pagination using a SQLAlchemy Select + SQLModel Session.

    Mirrors :func:`paginate_query` but takes an expression-language stmt
    instead of raw SQL. Use this for new blueprint code.

    Args:
        session: ``sqlmodel.Session``.
        stmt: ``sqlmodel.select(...)`` statement WITHOUT LIMIT/OFFSET.
            Must include its own ORDER BY so deterministic pages are
            possible.
        page: 0-based page index.
        size: rows per page (already clamped).
        row_transform: optional ``dict -> dict`` applied to each item
            after ``row_to_dict``.
        row_to_dict: optional ``row -> dict`` converter. Defaults to
            `dict(row._mapping)` for row-mapping rows, or scraped
            `__dict__` for scalar model instances.
    """
    from sqlalchemy import func as _func, select as _sa_select

    # Count — wrap the stmt in a subquery so ORDER BY / GROUP BY inside
    # don't confuse COUNT(*) OVER the filtered set.
    count_stmt = _sa_select(_func.count()).select_from(stmt.subquery())
    raw_total = session.exec(count_stmt).one()
    # `.one()` returns either an int (SQLModel unwraps single-column
    # selects) or a Row / tuple (SA `_sa_select` with an explicit
    # func.count expression). Unwrap the Row / tuple path so `total`
    # is always a bare int.
    if isinstance(raw_total, int):
        total = raw_total
    else:
        # Row, tuple, or any sequence-like single-column carrier.
        total = int(raw_total[0])

    offset = page * size
    raw_rows = session.exec(stmt.limit(size).offset(offset)).all()

    def _default_to_dict(r):
        if hasattr(r, "_mapping"):
            return dict(r._mapping)
        if hasattr(r, "__dict__"):
            return {k: v for k, v in r.__dict__.items() if not k.startswith("_sa_")}
        return dict(r) if isinstance(r, dict) else {"value": r}

    converter = row_to_dict or _default_to_dict
    items = [converter(r) for r in raw_rows]
    if row_transform is not None:
        items = [row_transform(it) for it in items]

    return Page(
        items=items, total=total, page=page, size=size,
        has_next=(offset + len(items)) < total,
        has_prev=page > 0,
    )


# ── Keyset / cursor ───────────────────────────────────────────

@dataclass
class CursorPage:
    items: list[Any]
    next_cursor: str | None   # opaque base64 token; None = no more pages
    size: int
    # Optional metadata — included when cheap to compute.
    approximate_total: int | None = None

    def to_envelope(self) -> dict:
        return {
            'items': self.items,
            'pagination': {
                'strategy': 'cursor',
                'size': self.size,
                'next_cursor': self.next_cursor,
                'has_next': self.next_cursor is not None,
                'approximate_total': self.approximate_total,
            },
        }


def encode_cursor(keys: list) -> str:
    """Serialize the last-row sort key(s) into an opaque token.

    We use JSON inside base64 — not because it needs to be opaque (the
    frontend doesn't care), but because it avoids exposing table column
    shape in URLs and makes the token self-describing when debugging."""
    return base64.urlsafe_b64encode(
        json.dumps(keys, default=str).encode('utf-8')
    ).decode('ascii')


def decode_cursor(token: str | None) -> list | None:
    """Inverse of encode_cursor. Returns None for a missing or malformed
    token — callers should treat "malformed" the same as "first page" so
    a stale URL can't hang the dashboard."""
    if not token:
        return None
    try:
        raw = base64.urlsafe_b64decode(token.encode('ascii'))
        decoded = json.loads(raw.decode('utf-8'))
        return decoded if isinstance(decoded, list) else None
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


def keyset_page(conn, base_sql: str, base_params: list,
                order_cols: list[tuple[str, str]],
                cursor_token: str | None, size: int,
                row_transform=None) -> CursorPage:
    """Run a keyset-paginated query against ``base_sql``.

    Contract:
        * ``base_sql`` is a SELECT … FROM … [WHERE …] with NO ORDER BY
          and NO LIMIT — this helper appends both.
        * ``order_cols`` is a list of (column_sql, 'ASC'|'DESC') pairs.
          The final element MUST be a tiebreaker that is unique per row
          (typically a PK / rowid) so cursors never collide.
        * ``cursor_token`` is opaque; ``None`` means "first page".
        * We fetch (size + 1) rows so we can tell whether a next page
          exists without firing a second COUNT query.

    Why keyset over OFFSET: keyset stays O(index-seek) even at deep
    offsets, and cannot double-show a row that another process just
    inserted above the current page. This matters for ``rule_triggers``
    and ``session_spans``, both of which grow while the user browses.
    """
    cursor_vals = decode_cursor(cursor_token)

    where_clause, predicate_params = _build_keyset_predicate(order_cols, cursor_vals)
    params = list(base_params) + predicate_params

    # Inject the keyset predicate. We always wrap it in its own
    # parenthesized group appended with AND, then let the caller's base
    # WHERE stand on its own — avoids quoting the existing clause.
    if where_clause:
        if ' where ' in base_sql.lower():
            sql = f"{base_sql} AND {where_clause}"
        else:
            sql = f"{base_sql} WHERE {where_clause}"
    else:
        sql = base_sql

    order_clause = ', '.join(f"{col} {direction}" for col, direction in order_cols)
    sql = f"{sql} ORDER BY {order_clause} LIMIT ?"
    params.append(size + 1)

    rows = conn.execute(sql, params).fetchall()
    items = [dict(r) for r in rows]
    if row_transform is not None:
        items = [row_transform(r) for r in items]

    has_more = len(items) > size
    if has_more:
        items = items[:size]

    return CursorPage(
        items=items,
        next_cursor=_next_cursor(items, order_cols, has_more),
        size=size,
    )


def _build_keyset_predicate(
    order_cols: list[tuple[str, str]], cursor_vals: list | None
) -> tuple[str | None, list]:
    """Build the keyset row-comparison WHERE predicate and its params.

    Returns ``(None, [])`` when there is no usable cursor (first page, or a
    cursor whose arity no longer matches ``order_cols``).
    """
    if cursor_vals is None or len(cursor_vals) != len(order_cols):
        return None, []

    # Build the row-comparison predicate the long way. We can't rely
    # on SQLite's row-value comparison (`(a, b) < (?, ?)`) for mixed
    # DESC/ASC columns, so we expand into the equivalent OR-of-ANDs.
    # For (c1 DESC, c2 DESC) this produces:
    #     c1 < ? OR (c1 = ? AND c2 < ?)
    or_terms = []
    params: list = []
    for i, ((col, direction), val) in enumerate(zip(order_cols, cursor_vals)):
        cmp_op = '<' if direction.upper() == 'DESC' else '>'
        eq_prefix = ' AND '.join(f"{c} = ?" for c, _ in order_cols[:i])
        term = f"{col} {cmp_op} ?"
        if eq_prefix:
            term = f"({eq_prefix} AND {term})"
        or_terms.append(term)
        # Equality prefix params, then the strict-compare param.
        for j in range(i):
            params.append(cursor_vals[j])
        params.append(val)
    return f"({' OR '.join(or_terms)})", params


def _next_cursor(
    items: list[dict], order_cols: list[tuple[str, str]], has_more: bool
) -> str | None:
    """Encode the next-page cursor from the last item, or ``None`` at the end."""
    if not (has_more and items):
        return None
    last = items[-1]
    # For the cursor we need the *raw* column values as they sit in
    # the DB, indexed by the SQL expression the caller used. If the
    # expression is something compound like ``start_time`` it must
    # appear as that literal key in each row dict — the caller's
    # SELECT list is responsible for exposing it.
    key_vals = [_extract_key(last, col) for col, _ in order_cols]
    return encode_cursor(key_vals)


def _extract_key(row: dict, col_sql: str):
    """Pull the cursor key out of a result row.

    The caller names the column the same way in ORDER BY and in the
    SELECT list; we match by that name. For qualified names like
    ``session_spans.start_time`` we also try the bare suffix so a caller
    doesn't have to alias every column."""
    if col_sql in row:
        return row[col_sql]
    bare = col_sql.rsplit('.', 1)[-1]
    if bare in row:
        return row[bare]
    raise KeyError(
        f"keyset column {col_sql!r} not present in result row; "
        f"alias it in the SELECT list (e.g. `{col_sql} AS {bare}`)"
    )


# ── SQLModel / SQLAlchemy expression variant ───────────────────

def _apply_keyset_predicate_stmt(stmt, order_cols, cursor_vals):
    """Append the keyset OR-of-ANDs WHERE predicate to ``stmt``.

    Mirrors :func:`_build_keyset_predicate` but for SQLAlchemy column
    expressions. Returns ``stmt`` unchanged when the cursor is absent or
    its arity no longer matches ``order_cols`` (first page / stale token).
    """
    if cursor_vals is None or len(cursor_vals) != len(order_cols):
        return stmt
    # Build the OR-of-ANDs predicate so we can handle mixed DESC/ASC.
    from sqlalchemy import and_, or_
    or_terms = []
    for i, ((col_obj, direction), val) in enumerate(zip(order_cols, cursor_vals)):
        strict = col_obj < val if direction.upper() == "DESC" else col_obj > val
        prefix = [order_cols[j][0] == cursor_vals[j] for j in range(i)]
        term = and_(*prefix, strict) if prefix else strict
        or_terms.append(term)
    return stmt.where(or_(*or_terms))


def _apply_keyset_order_stmt(stmt, order_cols, size):
    """Append ORDER BY (mixed ASC/DESC) and ``LIMIT size + 1`` to ``stmt``.

    The extra row lets the caller detect "has more" without a COUNT.
    """
    order_exprs = []
    for col_obj, direction in order_cols:
        if direction.upper() == "DESC":
            order_exprs.append(col_obj.desc())
        else:
            order_exprs.append(col_obj.asc())
    return stmt.order_by(*order_exprs).limit(size + 1)


def _default_stmt_to_dict(r):
    """Default row→dict converter for :func:`keyset_page_stmt`.

    Handles row-mapping results, scalar model instances (stripping
    SQLAlchemy's ``_sa_`` internals), plain dicts, and bare scalars.
    """
    if hasattr(r, "_mapping"):
        return dict(r._mapping)
    if hasattr(r, "__dict__"):
        return {k: v for k, v in r.__dict__.items() if not k.startswith("_sa_")}
    return dict(r) if isinstance(r, dict) else {"value": r}


def _next_cursor_stmt(items, order_cols, has_more):
    """Encode the next-page cursor from the last item, or ``None`` at the end.

    Mirrors :func:`_next_cursor` but resolves each column's lookup key
    from the SQLAlchemy expression's ``key`` (falling back to the bare
    attribute suffix).
    """
    if not (has_more and items):
        return None
    last = items[-1]
    key_vals = []
    for col_obj, _ in order_cols:
        key_name = getattr(col_obj, "key", None) or str(col_obj).split(".")[-1]
        key_vals.append(_extract_key(last, key_name))
    return encode_cursor(key_vals)


def keyset_page_stmt(session, stmt, order_cols, cursor_token, size,
                     row_transform=None, row_to_dict=None):
    """Keyset pagination using a SQLAlchemy Select + SQLModel Session.

    Mirrors :func:`keyset_page` but takes an expression-language stmt
    instead of raw SQL. Use this for new blueprint code and migrate
    existing keyset_page call sites when convenient — both live side
    by side during the Phase B migration.

    Args:
        session: ``sqlmodel.Session`` (or SQLAlchemy ``Session``).
        stmt: ``sqlmodel.select(...)`` statement WITHOUT ORDER BY or
            LIMIT. Whatever columns the caller projects — typically a
            model class or a column tuple — must include every column
            named in ``order_cols`` so the cursor-key extraction can
            find them.
        order_cols: list of ``(column_object, 'ASC'|'DESC')`` tuples.
            The column object is an SQLAlchemy expression; e.g.
            ``(RuleTrigger.checked_at, 'DESC')``. The final entry must
            be a unique tiebreaker.
        cursor_token: opaque token from the previous page, or ``None``.
        size: rows per page (already clamped).
        row_transform: optional ``dict -> dict`` applied after
            ``row_to_dict`` to each item.
        row_to_dict: required when the stmt returns rows that aren't
            trivially dict-compatible. Defaults to ``dict(row._mapping)``
            for row-mapping results, or model ``__dict__`` for scalar
            model instances.

    Returns:
        :class:`CursorPage` with next_cursor / size / items.
    """
    cursor_vals = decode_cursor(cursor_token)

    stmt = _apply_keyset_predicate_stmt(stmt, order_cols, cursor_vals)
    stmt = _apply_keyset_order_stmt(stmt, order_cols, size)

    raw_rows = session.exec(stmt).all()

    converter = row_to_dict or _default_stmt_to_dict
    items = [converter(r) for r in raw_rows]
    if row_transform is not None:
        items = [row_transform(it) for it in items]

    has_more = len(items) > size
    if has_more:
        items = items[:size]

    next_token = _next_cursor_stmt(items, order_cols, has_more)
    return CursorPage(items=items, next_cursor=next_token, size=size)
