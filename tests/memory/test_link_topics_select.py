"""Row selection for `regin memory link-topics` — the orphans-only / kind /
tier filters added on top of the plain active list."""

from __future__ import annotations

import lib.memory as memory
from cli.commands.memory import _select_link_rows


def _remember(body, **kw):
    # link-topics selects via list_memories(include_tests=False), so the rows
    # under test must be non-test memories (the tmp_memory_db fixture isolates
    # them to this test's DB regardless).
    kw.setdefault("is_test", False)
    return memory.remember(body, **kw)


def _ids(rows):
    return {m["id"] for m in rows}


def test_select_default_returns_all_active():
    a = _remember("First lesson body.", kind="lesson")
    b = _remember("Second lesson body.", kind="lesson")
    store = memory.get_store()

    rows = _select_link_rows(store, scope=None, kind=None, tier=None,
                             limit=100, orphans_only=False)

    assert _ids(rows) == {a, b}


def test_orphans_only_excludes_already_linked():
    linked = _remember("Already filed under a topic.", kind="lesson")
    orphan = _remember("Never filed anywhere.", kind="lesson")
    store = memory.get_store()
    store.link_authoritative_topic(linked, "agent-memory", source="route")

    rows = _select_link_rows(store, scope=None, kind=None, tier=None,
                             limit=100, orphans_only=True)

    assert _ids(rows) == {orphan}
    # the unfiltered selection still sees both
    allrows = _select_link_rows(store, scope=None, kind=None, tier=None,
                                limit=100, orphans_only=False)
    assert _ids(allrows) == {linked, orphan}


def test_orphans_only_empty_when_everything_filed():
    a = _remember("Filed A.", kind="lesson")
    b = _remember("Filed B.", kind="lesson")
    store = memory.get_store()
    store.link_authoritative_topic(a, "agent-memory", source="route")
    store.link_authoritative_topic(b, "agent-memory", source="route")

    rows = _select_link_rows(store, scope=None, kind=None, tier=None,
                             limit=100, orphans_only=True)

    assert rows == []


def test_orphans_only_respects_scope():
    in_scope = _remember("Orphan in scope.", kind="lesson", scope="repo:x")
    _remember("Orphan other scope.", kind="lesson", scope="repo:y")
    store = memory.get_store()

    rows = _select_link_rows(store, scope="repo:x", kind=None, tier=None,
                             limit=100, orphans_only=True)

    assert _ids(rows) == {in_scope}


def test_kind_filter_passes_through():
    lesson = _remember("A lesson.", kind="lesson")
    _remember("A gotcha.", kind="gotcha")
    store = memory.get_store()

    rows = _select_link_rows(store, scope=None, kind="lesson", tier=None,
                             limit=100, orphans_only=False)

    assert _ids(rows) == {lesson}
