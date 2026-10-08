"""R7 — what a typed reference resolves to.

    #a1          keep typing: 1–3 characters could be several sessions, so no
                 query runs
    #a1b2…       sessions whose id starts with that prefix
    F146 / f:146 that feature (T… task, D… defect); on a miss, "No task T999"
    146          one row per kind that has id 146

Every numeric form is followed by the text that mentions the number, grouped
by kind and labelled `text`. A miss never offers the nearest ids: T998 is not
a better answer to T999 than nothing, it is a different task.

Anything else is not an address (`address: None`) and the caller searches it
as text. Nothing here writes.
"""

from __future__ import annotations

import re

from lib.fulltext.docs import (
    KIND_DEFECT, KIND_FEATURE, KIND_SESSION, KIND_TASK, LIKE_ESCAPE,
    MIN_QUERY_CHARS,
)
from lib.fulltext.entity_docs import ENTITY_SEARCHES, escape_like
from lib.fulltext.session_docs import search_sessions
from lib.palette import rows as palette_rows
from lib.trace.alias import trace_group

# The `#` address answers exactly as `/api/session-switcher?id=` does
# (`MIN_ID_CHARS`, `ID_LOOKUP_LIMIT`, `_rows_by_id_prefix` in
# web/blueprints/trace/switcher.py): same floor, same cap, any characters
# rather than only hex — a hand-made or `sdk-` trace id is still pasteable —
# fixtures out, and an alias pair folded onto its canonical session.
MIN_SESSION_PREFIX = 4
SESSION_PREFIX_LIMIT = 12
TEXT_CAP = 3
KEEP_TYPING = "Keep typing the session id"

# 18 digits keeps an id inside SQLite's 64-bit integer; a longer run of
# digits is not anything's id and is searched as text instead.
_ENTITY_REF = re.compile(r"^(?P<prefix>[ftd]):?(?P<id>\d{1,18})$", re.IGNORECASE)
_NUMBER = re.compile(r"^\d{1,18}$")
_HASH = re.compile(r"^#(?P<prefix>\S*)$")

_KIND_OF_PREFIX = {"f": KIND_FEATURE, "t": KIND_TASK, "d": KIND_DEFECT}
_ID_KINDS = (KIND_FEATURE, KIND_TASK, KIND_DEFECT)
_TEXT_KINDS = (*_ID_KINDS, KIND_SESSION)
_ROWS = {
    KIND_FEATURE: palette_rows.feature_rows,
    KIND_TASK: palette_rows.task_rows,
    KIND_DEFECT: palette_rows.defect_rows,
    KIND_SESSION: palette_rows.session_rows,
}
_REF_PREFIX = {KIND_FEATURE: "F", KIND_TASK: "T", KIND_DEFECT: "D"}


def _payload(ref: str, address, hits=(), text=(), **extra) -> dict:
    out = {"ref": ref, "address": address, "hits": list(hits), "text": list(text)}
    out.update({k: v for k, v in extra.items() if v})
    return out


def _labelled(row: dict, match: str) -> dict:
    return {**row, "match": match}


def _exact(conn, kind: str, ident: int) -> dict | None:
    row = _ROWS[kind](conn, [ident]).get(ident)
    return _labelled(row, "id") if row else None


def _real_sessions(conn, trace_ids: list[str]) -> set[str]:
    if not trace_ids:
        return set()
    marks = ",".join("?" * len(trace_ids))
    return {r[0] for r in conn.execute(
        f"SELECT trace_id FROM sessions WHERE trace_id IN ({marks}) AND is_test = 0",
        trace_ids)}


def _mentions(conn, kind: str, needle: str) -> list[str]:
    """Ids of `kind` whose text holds `needle` as a whole number — `F1460`
    does not mention 146. Sessions are held to the `#` path's rule: fixtures
    and traces with no session row are not something to navigate to."""
    hits = (search_sessions(conn, needle) if kind == KIND_SESSION
            else ENTITY_SEARCHES[kind](conn, needle))
    whole = re.compile(rf"(?<!\d){re.escape(needle)}(?!\d)")
    ids = [h.doc.id for h in hits
           if whole.search(f"{h.doc.title}\n{h.doc.body}")]
    if kind == KIND_SESSION:
        real = _real_sessions(conn, ids)
        ids = [i for i in ids if i in real]
    return ids


def _text_groups(conn, needle: str, exclude: set[tuple[str, str]]) -> list[dict]:
    """Rows whose text mentions `needle`, one group per kind, skipping the
    rows already resolved by id."""
    if len(needle) < MIN_QUERY_CHARS:
        return []
    groups = []
    for kind in _TEXT_KINDS:
        ids = [i for i in _mentions(conn, kind, needle)
               if (kind, i) not in exclude]
        if not ids:
            continue
        page = ids[:TEXT_CAP]
        keys = page if kind == KIND_SESSION else [int(i) for i in page]
        built = _ROWS[kind](conn, keys)
        items = [_labelled(built[k], "text") for k in keys if k in built]
        groups.append({"kind": kind, "total": len(ids), "items": items})
    return groups


def _entity(conn, raw: str, kind: str, ident: int) -> dict:
    hit = _exact(conn, kind, ident)
    exclude = {(kind, str(ident))} if hit else set()
    text = _text_groups(conn, str(ident), exclude)
    miss = None if hit else f"No {kind} {_REF_PREFIX[kind]}{ident}"
    return _payload(raw, kind, [hit] if hit else [], text, miss=miss)


def _number(conn, raw: str, ident: int) -> dict:
    hits = [row for kind in _ID_KINDS if (row := _exact(conn, kind, ident))]
    exclude = {(row["kind"], str(ident)) for row in hits}
    text = _text_groups(conn, str(ident), exclude)
    miss = None if hits else f"Nothing has id {ident}"
    return _payload(raw, "number", hits, text, miss=miss)


def _session_prefix(conn, raw: str, prefix: str) -> dict:
    if len(prefix) < MIN_SESSION_PREFIX:
        return _payload(raw, KIND_SESSION, pending=KEEP_TYPING)
    matched = [r[0] for r in conn.execute(
        f"SELECT trace_id FROM sessions WHERE trace_id LIKE ? ESCAPE '{LIKE_ESCAPE}' "
        "AND is_test = 0 ORDER BY last_seen DESC, trace_id LIMIT ?",
        (f"{escape_like(prefix)}%", SESSION_PREFIX_LIMIT * 2))]
    matches = list(dict.fromkeys(trace_group(conn, t)[0] for t in matched))
    matches = matches[:SESSION_PREFIX_LIMIT]
    built = palette_rows.session_rows(conn, matches)
    hits = [_labelled(built[t], "prefix") for t in matches if t in built]
    miss = None if hits else f"No session {raw}"
    return _payload(raw, KIND_SESSION, hits, miss=miss)


def resolve(conn, ref: str) -> dict:
    raw = (ref or "").strip()
    if found := _HASH.match(raw):
        return _session_prefix(conn, raw, found["prefix"])
    if found := _ENTITY_REF.match(raw):
        kind = _KIND_OF_PREFIX[found["prefix"].lower()]
        return _entity(conn, raw, kind, int(found["id"]))
    if _NUMBER.match(raw):
        return _number(conn, raw, int(raw))
    return _payload(raw, None)
