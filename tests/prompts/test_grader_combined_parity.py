"""Characterization test: the editable ``grader-combined-*`` skeletons render
byte-identical to the pre-refactor hardcoded combined-judge prompt fragments
(``lib/grader/combined_agentic.py``'s module constants + ``build_combined_prompt``).
"""

from __future__ import annotations

from lib.grader.combined_agentic import (
    _CORRECTNESS_BLOCK, _PROCESS_BLOCK, _ROLE, _aspects_block, _output_block,
    build_combined_prompt,
)
from lib.prompts import render_surface
from lib.prompts.surfaces.grader import (
    COMBINED_CORRECTNESS_SURFACE_ID, COMBINED_PROCESS_SURFACE_ID,
    COMBINED_ROLE_SURFACE_ID,
)


def test_role_surface_matches_frozen_constant():
    assert render_surface(COMBINED_ROLE_SURFACE_ID, {}) == _ROLE


def test_correctness_block_surface_matches_frozen_constant():
    assert render_surface(COMBINED_CORRECTNESS_SURFACE_ID, {}) == _CORRECTNESS_BLOCK


def test_process_block_surface_matches_frozen_constant():
    assert render_surface(COMBINED_PROCESS_SURFACE_ID, {}) == _PROCESS_BLOCK


def _reference_prompt(trace_id, python, axes, aspect_defs):
    parts = [_ROLE.replace("{trace_id}", trace_id).replace("{python}", python)]
    if "correctness" in axes:
        parts.append(_CORRECTNESS_BLOCK)
    if "process" in axes:
        parts.append(_PROCESS_BLOCK)
    if aspect_defs:
        parts.append(_aspects_block(aspect_defs))
    parts.append(_output_block(axes, aspect_defs))
    return "\n\n".join(parts)


def test_build_combined_prompt_correctness_and_process():
    got = build_combined_prompt("T-42", ".venv/bin/python",
                                ("correctness", "process"), [])
    assert got == _reference_prompt("T-42", ".venv/bin/python",
                                    ("correctness", "process"), [])


def test_build_combined_prompt_correctness_only():
    got = build_combined_prompt("T-1", ".venv/bin/python", ("correctness",), [])
    assert got == _reference_prompt("T-1", ".venv/bin/python", ("correctness",), [])
    assert "<process>" not in got


def test_build_combined_prompt_with_aspects():
    aspect_defs = [("safety", "Safety", "no destructive actions")]
    got = build_combined_prompt("T-9", "py", ("correctness",), aspect_defs)
    assert got == _reference_prompt("T-9", "py", ("correctness",), aspect_defs)
    assert "<aspects>" in got
