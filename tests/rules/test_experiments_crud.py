"""Unit tests for lib.experiments CRUD + activation invariants.

Covers the post-B.3.5 SQLModel implementation. apply_conceal +
list_sections are already tested in tests/test_experiments.py; this
file adds the DB-touching CRUD surface.
"""

from __future__ import annotations

import json

from lib.experiments import (
    activate, create, deactivate, delete, get, get_active,
    list_all, list_for_pattern, patterns_with_active, update,
)


# ── create + get ─────────────────────────────────────────────

def test_create_returns_new_id(tmp_db):
    experiment_id = create("my-pattern", "conceal-disciplines", ["## Disciplines"])
    assert experiment_id > 0
    row = get(experiment_id)
    assert row is not None
    assert row["pattern_slug"] == "my-pattern"
    assert row["name"] == "conceal-disciplines"
    assert row["sections"] == ["## Disciplines"]
    assert row["active"] == 0


def test_get_missing_returns_none(tmp_db):
    assert get(9999) is None


# ── listing ───────────────────────────────────────────────────

def test_list_all_orders_by_pattern_then_created_desc(tmp_db):
    _ = create("b-pattern", "exp1", ["## H1"])
    _ = create("a-pattern", "exp1", ["## H1"])
    _ = create("a-pattern", "exp2", ["## H2"])
    rows = list_all()
    # Sorted by pattern_slug ascending, then created_at desc within a slug.
    slugs = [r["pattern_slug"] for r in rows]
    assert slugs == sorted(slugs)


def test_list_for_pattern_filters(tmp_db):
    create("alpha", "e1", ["## A"])
    create("beta", "e2", ["## B"])
    rows = list_for_pattern("alpha")
    assert len(rows) == 1
    assert rows[0]["pattern_slug"] == "alpha"


# ── activate / deactivate ────────────────────────────────────

def test_activate_returns_slug_and_flips_active_bit(tmp_db):
    eid = create("p1", "e1", ["## H"])
    slug = activate(eid)
    assert slug == "p1"
    row = get(eid)
    assert row["active"] == 1
    assert row["activated_at"] is not None


def test_activate_enforces_one_active_per_pattern(tmp_db):
    a = create("p1", "eA", ["## A"])
    b = create("p1", "eB", ["## B"])
    activate(a)
    assert get(a)["active"] == 1
    # Activating b deactivates a within the same transaction.
    activate(b)
    assert get(a)["active"] == 0
    assert get(b)["active"] == 1


def test_activate_missing_returns_none(tmp_db):
    assert activate(9999) is None


def test_deactivate_clears_bit_and_activated_at(tmp_db):
    eid = create("p1", "e1", ["## H"])
    activate(eid)
    assert deactivate(eid) == "p1"
    row = get(eid)
    assert row["active"] == 0
    assert row["activated_at"] is None


# ── patterns_with_active ─────────────────────────────────────

def test_patterns_with_active(tmp_db):
    a = create("p-has-active", "e", ["## H"])
    create("p-no-active", "e", ["## H"])
    activate(a)
    active = patterns_with_active()
    assert "p-has-active" in active
    assert "p-no-active" not in active


# ── get_active ───────────────────────────────────────────────

def test_get_active_returns_id_and_sections(tmp_db):
    eid = create("pp", "ee", ["## X", "## Y"])
    activate(eid)
    found = get_active("pp")
    assert found is not None
    assert found == (eid, ["## X", "## Y"])


def test_get_active_none_when_no_active_experiment(tmp_db):
    create("pp", "ee", ["## X"])
    # Not activated.
    assert get_active("pp") is None


# ── update ───────────────────────────────────────────────────

def test_update_rewrites_name_and_sections(tmp_db):
    eid = create("pp", "original", ["## A"])
    slug = update(eid, "renamed", ["## B", "## C"])
    assert slug == "pp"
    row = get(eid)
    assert row["name"] == "renamed"
    assert row["sections"] == ["## B", "## C"]
    assert json.loads(row["conceal_spec"]) == ["## B", "## C"]


def test_update_missing_returns_none(tmp_db):
    assert update(9999, "x", ["## Y"]) is None


# ── delete ────────────────────────────────────────────────────

def test_delete_removes_row(tmp_db):
    eid = create("pp", "ee", ["## H"])
    delete(eid)
    assert get(eid) is None


def test_delete_missing_is_noop(tmp_db):
    # Should not raise; nothing to delete.
    delete(9999)
