"""Render test for the `memory-reflect-dream` surface: the single reflect
consolidation prompt. Pins the slots and the load-bearing instructions (the
strict-JSON output contract, the conservative defaults) so a prompt edit that
breaks the plan parser fails here first."""

from __future__ import annotations

from lib.prompts import render_surface
from lib.prompts.surfaces.memory import DREAM_SURFACE_ID


def _render():
    return render_surface(DREAM_SURFACE_ID, {
        "working_block": "WORKING-BLOCK-SENTINEL",
        "pairs_block": "PAIRS-BLOCK-SENTINEL",
        "python": ".venv/bin/python",
    })


def test_dream_surface_renders_all_slots():
    out = _render()
    assert "WORKING-BLOCK-SENTINEL" in out
    assert "PAIRS-BLOCK-SENTINEL" in out
    assert ".venv/bin/python cli/regin.py memory recall" in out


def test_dream_surface_carries_the_plan_contract():
    out = _render()
    # The strict-JSON output contract the plan parser depends on.
    assert "Respond with ONE JSON object and NOTHING else" in out
    assert '"actions"' in out
    assert '"promote|hold|drop|merge"' in out
    assert '"contradict|obsolete|distinct"' in out
    assert '"synthesize"' in out


def test_dream_surface_states_conservative_defaults():
    out = _render()
    assert "Choose this whenever you are unsure" in out
    assert "never force an" in out
