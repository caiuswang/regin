"""seed_builtin_skeletons healing of retired default bodies.

`render_surface` prefers the stored `prompt_templates` row and the seeder
historically only inserted *missing* slugs, so a code upgrade that revised a
builtin prompt never reached an existing install — the stale seed silently
pinned the old body forever. The heal path recognises an un-edited stale row
(its body hashes to a registered RETIRED default) and overwrites it with the
current default; user-edited bodies never match and survive.
"""

from __future__ import annotations

from sqlmodel import select

from lib.orm import SessionLocal
from lib.orm.models import PromptTemplate
from lib.prompt_templates import (get_template_by_slug, seed_builtin_skeletons,
                                  update_template)
from lib.prompts.registry import get_surface

_SLUG = "memory-reflect-contradiction"

# The retired 2-way default body verbatim (its sha256 is registered via
# `register_retired_default` in lib/prompts/surfaces/memory.py).
_OLD_DEFAULT = (
    "Two memory entries from past coding sessions follow. Answer with "
    "exactly one word — CONTRADICT if they make incompatible claims "
    "about the same thing, or DISTINCT otherwise.\n\n"
    "A: {{memory_a}}\n\nB: {{memory_b}}\n"
)


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


def test_seed_heals_unedited_stale_builtin(tmp_db):
    seed_builtin_skeletons()
    # Simulate a pre-upgrade install: the stored builtin row still carries
    # the retired default body, verbatim as the old seeder wrote it.
    _write_body_verbatim(_SLUG, _OLD_DEFAULT)
    healed = seed_builtin_skeletons()
    assert healed == 1
    row = get_template_by_slug(_SLUG)
    assert row["body"] == get_surface(_SLUG).default_body()
    # And the healed row is stable: a further re-seed changes nothing.
    assert seed_builtin_skeletons() == 0


def test_seed_leaves_user_edited_body_alone(tmp_db):
    seed_builtin_skeletons()
    update_template(_SLUG, {"body": "MY CUSTOM JUDGE {{memory_a}} vs {{memory_b}}"})
    assert seed_builtin_skeletons() == 0
    assert get_template_by_slug(_SLUG)["body"].startswith("MY CUSTOM JUDGE")
