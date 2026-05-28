"""Unit tests for lib.audit.

Covers log_action (write-side, best-effort) and get_log / get_log_page
(read-side, pagination + filters). Uses tmp_db for isolation.
"""

from __future__ import annotations

import json

from lib.audit import get_log, get_log_page, log_action


# ── log_action ───────────────────────────────────────────────

def test_log_action_writes_row(tmp_db):
    log_action(
        user_id=None, username="alice", action="deploy",
        target="pattern:foo", detail="manual push",
    )
    rows = get_log(limit=10)
    assert len(rows) == 1
    assert rows[0]["action"] == "deploy"
    assert rows[0]["target"] == "pattern:foo"
    assert rows[0]["detail"] == "manual push"


def test_log_action_serialises_dict_detail_as_json(tmp_db):
    log_action(None, "anon", "edit_rule", "rules/foo",
               detail={"scope": "global", "id": 5})
    rows = get_log(limit=1)
    assert rows[0]["detail"] is not None
    parsed = json.loads(rows[0]["detail"])
    assert parsed == {"scope": "global", "id": 5}


def test_log_action_null_user_id_allowed(tmp_db):
    log_action(None, "anon", "login", "users/anon")
    rows = get_log(limit=1)
    assert rows[0]["user_id"] is None
    assert rows[0]["username"] == "anon"


# ── get_log + get_log_page ───────────────────────────────────

def test_get_log_empty(tmp_db):
    assert get_log() == []


def test_get_log_page_returns_items_and_total(tmp_db):
    for i in range(5):
        log_action(None, "alice", f"action-{i}", f"target-{i}")
    items, total = get_log_page(page=0, size=3)
    assert total == 5
    assert len(items) == 3


def test_get_log_page_pagination(tmp_db):
    for i in range(7):
        log_action(None, "alice", f"a-{i}", f"t-{i}")
    items0, total0 = get_log_page(page=0, size=3)
    items1, total1 = get_log_page(page=1, size=3)
    items2, total2 = get_log_page(page=2, size=3)
    assert total0 == total1 == total2 == 7
    assert len(items0) == 3
    assert len(items1) == 3
    assert len(items2) == 1
    # All rows should be disjoint.
    ids = [r["id"] for r in items0 + items1 + items2]
    assert len(set(ids)) == len(ids)


def test_get_log_page_filter_by_user(tmp_db):
    log_action(None, "alice", "x", "a")
    log_action(None, "bob", "x", "b")
    alice_items, alice_total = get_log_page(size=10, user="alice")
    assert alice_total == 1
    assert alice_items[0]["username"] == "alice"


def test_get_log_page_filter_by_action(tmp_db):
    log_action(None, "alice", "deploy", "a")
    log_action(None, "alice", "undeploy", "a")
    deploy_items, deploy_total = get_log_page(size=10, action="deploy")
    assert deploy_total == 1
    assert deploy_items[0]["action"] == "deploy"


def test_get_log_newest_first(tmp_db):
    log_action(None, "alice", "first", "x")
    log_action(None, "alice", "second", "x")
    log_action(None, "alice", "third", "x")
    rows = get_log(limit=10)
    # The legacy shape is newest-first via created_at DESC, id DESC.
    # Same-timestamp tie goes to the highest id.
    assert rows[0]["action"] == "third"
    assert rows[-1]["action"] == "first"
