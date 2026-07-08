"""seed_builtin_skeletons healing and dead-slug cleanup.

`render_surface` prefers the stored `prompt_templates` row and the seeder
historically only inserted *missing* slugs, so a code upgrade that revised a
builtin prompt never reached an existing install — the stale seed silently
pinned the old body forever. The heal path recognises an un-edited stale row
(its body hashes to a registered RETIRED default) and overwrites it with the
current default; when the slug itself was retired (surface deregistered) the
un-edited row is DELETED instead. User-edited bodies never match a retired
hash and always survive.
"""

from __future__ import annotations

import hashlib
from datetime import datetime

from sqlmodel import select

from lib.orm import SessionLocal
from lib.orm.models import PromptTemplate
from lib.prompt_templates import (get_template_by_slug, seed_builtin_skeletons,
                                  update_template)
from lib.prompts import registry
from lib.prompts.registry import get_surface, retired_default_hashes

_LIVE_SLUG = "memory-distill"
_DEAD_SLUG = "memory-reflect-contradiction"

# The dead slug's last-shipped default verbatim (its sha256 is registered in
# lib/prompts/surfaces/memory.py::_RETIRED_SLUG_HASHES) — the body an
# un-edited pre-dream install still stores.
_DEAD_SLUG_LAST_DEFAULT = (
    "Two memory entries from past coding sessions follow, each with the "
    "time it was recorded. A is the OLDER entry, B the NEWER. Answer with "
    "exactly one word:\n"
    "- CONTRADICT: they make incompatible claims about the same thing.\n"
    "- OBSOLETE: B describes a later change, fix, or removal that "
    "supersedes what A records.\n"
    "- DISTINCT: neither — they can both stand.\n\n"
    "A (recorded {{created_a}}): {{memory_a}}\n\n"
    "B (recorded {{created_b}}): {{memory_b}}\n"
)

_FAKE_OLD_DEFAULT = "A superseded default body {{trace_id}} {{python}}\n"


def _register_fake_retired(monkeypatch, slug, body):
    """Register `body`'s hash as a retired default for `slug`, restored after
    the test (setitem replaces the whole per-slug set with a copy)."""
    h = hashlib.sha256(body.encode("utf-8")).hexdigest()
    monkeypatch.setitem(registry._RETIRED_DEFAULT_HASHES, slug,
                        retired_default_hashes(slug) | {h})


def _write_body_verbatim(slug, body):
    """Overwrite the stored body byte-for-byte, the way the seeder wrote it.
    (`update_template` strips whitespace, so it can't reproduce a seeded
    pre-upgrade row exactly.)"""
    with SessionLocal() as session:
        row = session.exec(select(PromptTemplate)
                           .where(PromptTemplate.slug == slug)).one()
        row.body = body
        session.add(row)
        session.commit()


def _insert_builtin_row(slug, body):
    """A builtin skeleton row for a slug the seeder no longer knows —
    simulating a pre-upgrade install whose surface was since retired."""
    now = datetime.now().isoformat()
    with SessionLocal() as session:
        session.add(PromptTemplate(
            slug=slug, label="Retired surface", body=body, kind="skeleton",
            variables="[]", applies_to="[]", default_for_providers="[]",
            builtin=1, created_at=now, updated_at=now))
        session.commit()


def test_seed_heals_unedited_stale_builtin(tmp_db, monkeypatch):
    _register_fake_retired(monkeypatch, _LIVE_SLUG, _FAKE_OLD_DEFAULT)
    seed_builtin_skeletons()
    # Simulate a pre-upgrade install: the stored builtin row still carries
    # the retired default body, verbatim as the old seeder wrote it.
    _write_body_verbatim(_LIVE_SLUG, _FAKE_OLD_DEFAULT)
    healed = seed_builtin_skeletons()
    assert healed == 1
    row = get_template_by_slug(_LIVE_SLUG)
    assert row["body"] == get_surface(_LIVE_SLUG).default_body()
    # And the healed row is stable: a further re-seed changes nothing.
    assert seed_builtin_skeletons() == 0


def test_seed_leaves_user_edited_body_alone(tmp_db, monkeypatch):
    _register_fake_retired(monkeypatch, _LIVE_SLUG, _FAKE_OLD_DEFAULT)
    seed_builtin_skeletons()
    update_template(_LIVE_SLUG, {"body": "MY CUSTOM PROMPT {{trace_id}}"})
    assert seed_builtin_skeletons() == 0
    assert get_template_by_slug(_LIVE_SLUG)["body"].startswith("MY CUSTOM")


def test_seed_deletes_unedited_dead_slug_row(tmp_db):
    seed_builtin_skeletons()
    _insert_builtin_row(_DEAD_SLUG, _DEAD_SLUG_LAST_DEFAULT)
    assert get_template_by_slug(_DEAD_SLUG) is not None
    changed = seed_builtin_skeletons()
    assert changed == 1
    assert get_template_by_slug(_DEAD_SLUG) is None
    assert seed_builtin_skeletons() == 0     # nothing left to delete


def test_seed_keeps_user_edited_dead_slug_row(tmp_db):
    seed_builtin_skeletons()
    _insert_builtin_row(_DEAD_SLUG, "MY HAND-TUNED JUDGE {{memory_a}}")
    assert seed_builtin_skeletons() == 0
    row = get_template_by_slug(_DEAD_SLUG)
    assert row is not None
    assert row["body"].startswith("MY HAND-TUNED")


def test_seed_keeps_unregistered_slug_without_retired_hashes(tmp_db):
    """An unregistered slug with NO registered retired hashes is not dead —
    it may belong to a surface that registers late (a plugin, a
    conditionally-loaded module) — so its builtin row is never deleted."""
    seed_builtin_skeletons()
    _insert_builtin_row("late-plugin-surface", "Plugin default body {{x}}")
    assert seed_builtin_skeletons() == 0
    assert get_template_by_slug("late-plugin-surface") is not None
