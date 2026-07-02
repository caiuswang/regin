"""Seeding + fallback-safety for the surface registry / resolver."""

from __future__ import annotations

import pytest

from lib.prompt_templates import (
    create_template,
    get_template_by_slug,
    reset_skeleton_to_default,
    seed_builtin_skeletons,
    update_template,
)
from lib.prompts import list_surfaces, render_surface
from lib.prompts.surfaces.drafting import SURFACE_ID as DRAFTING

# A full context so the drafting default body renders without an UnknownVariable.
_CTX = {
    "topic_request": "req",
    "prior_reference": "",
    "custom_instructions": "",
    "temp_output_path": "/tmp/out.tmp.json",
    "output_file": "/tmp/topics.json",
    "finish_cmd": "regin finish",
    "existing_topics_json": "[]",
    "buckets_json": "[]",
    "sibling_section": "",
}


def test_registry_lists_drafting_surface():
    ids = {s.id for s in list_surfaces()}
    assert DRAFTING in ids


def test_seed_is_idempotent_and_marks_builtin_skeleton(tmp_db):
    first = seed_builtin_skeletons()
    assert first >= 1
    assert seed_builtin_skeletons() == 0  # second run inserts nothing
    row = get_template_by_slug(DRAFTING)
    assert row is not None
    assert row["kind"] == "skeleton"
    assert row["builtin"] is True
    assert row["variables"], "skeleton row carries its variable palette"


def test_render_surface_uses_default_when_no_row(tmp_db):
    # No skeleton row seeded → falls back to the registry default body.
    out = render_surface(DRAFTING, _CTX)
    assert "Regin Topic Proposal Agent Task" in out
    assert "req" in out


def test_render_surface_uses_stored_row(tmp_db):
    seed_builtin_skeletons()
    update_template(DRAFTING, {"body": "CUSTOM {{topic_request}}"})
    assert render_surface(DRAFTING, _CTX) == "CUSTOM req"


def test_bad_user_edit_falls_back_to_default_not_error(tmp_db):
    seed_builtin_skeletons()
    # An unresolvable include is exactly the kind of broken edit a user can save.
    update_template(DRAFTING, {"body": "BROKEN {{include:does-not-exist}}"})
    out = render_surface(DRAFTING, _CTX)
    # Degrades to the built-in default rather than raising into the run.
    assert "Regin Topic Proposal Agent Task" in out
    assert "does-not-exist" not in out


def test_reset_skeleton_restores_default_body(tmp_db):
    seed_builtin_skeletons()
    update_template(DRAFTING, {"body": "CUSTOM"})
    restored = reset_skeleton_to_default(DRAFTING)
    assert "Regin Topic Proposal Agent Task" in restored["body"]


def test_include_resolves_fragment_from_db(tmp_db):
    create_template({"label": "Frag", "slug": "frag", "body": "HELLO {{topic_request}}"})
    seed_builtin_skeletons()
    update_template(DRAFTING, {"body": "pre {{include:frag}} post"})
    assert render_surface(DRAFTING, _CTX) == "pre HELLO req post"


def test_unknown_surface_raises():
    with pytest.raises(KeyError):
        render_surface("no-such-surface", {})


def test_missing_table_degrades_to_default(tmp_db, monkeypatch):
    # An abnormally initialized DB (no prompt_templates table) raises
    # OperationalError from the store read — render_surface must still fall back
    # to the built-in default, not crash the run.
    from sqlalchemy.exc import OperationalError

    import lib.prompts.resolve as resolve

    def _boom(_slug):
        raise OperationalError("SELECT ...", {}, Exception("no such table: prompt_templates"))

    monkeypatch.setattr("lib.prompt_templates.get_template_by_slug", _boom)
    out = resolve.render_surface(DRAFTING, _CTX)
    assert "Regin Topic Proposal Agent Task" in out
