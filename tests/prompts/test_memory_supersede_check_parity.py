"""Characterization test: the editable ``memory-supersede-check`` skeleton
renders byte-identical to the pre-refactor hardcoded supersede-conflict prompt
(``lib/memory/distill.py::_llm_says_supersedes``'s old f-string).
"""

from __future__ import annotations

from lib.prompts import render_surface
from lib.prompts.surfaces.memory import SUPERSEDE_CHECK_SURFACE_ID


def _reference_prompt(existing_title, existing_body, new_title, new_body):
    return (
        "An EXISTING memory and a NEW memory from coding sessions follow. "
        "Does the NEW one make a claim INCOMPATIBLE with the EXISTING one "
        "about the same thing (so the EXISTING one is now wrong)? Answer with "
        "exactly one word — CONTRADICT if incompatible, or CONSISTENT "
        "otherwise.\n\n"
        f"EXISTING: {existing_title}\n{existing_body[:1200]}"
        f"\n\nNEW: {new_title}\n{new_body[:1200]}\n"
    )


def _render(existing_title, existing_body, new_title, new_body):
    return render_surface(SUPERSEDE_CHECK_SURFACE_ID, {
        "existing_title": existing_title,
        "existing_body": existing_body[:1200],
        "new_title": new_title,
        "new_body": new_body[:1200],
    })


def test_parity_typical():
    args = ("Run the suite with pytest directly",
            "Run regin's test suite by invoking pytest directly.",
            "Never run the suite with pytest directly",
            "Use `.venv/bin/python -m pytest` from the repo root instead.")
    assert _render(*args) == _reference_prompt(*args)


def test_parity_empty_fields():
    assert _render("", "", "", "") == _reference_prompt("", "", "", "")


def test_parity_clips_long_body_to_1200_chars():
    long_body = "z" * 2000
    args = ("Existing", long_body, "New", long_body)
    got = _render(*args)
    assert got == _reference_prompt(*args)
    assert got.count("z") == 2400  # two 1200-char bodies, both clipped
