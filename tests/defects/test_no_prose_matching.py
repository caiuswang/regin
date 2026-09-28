"""Defects are declared, never harvested from what someone typed."""

from __future__ import annotations

from pathlib import Path


def test_no_module_matches_defect_reports_against_prompt_text():
    """CAI-207's prose matching is gone, and must not come back: rounds are
    read from declared transitions, never inferred from what someone typed."""
    import lib.defects as package  # noqa: PLC0415

    assert not hasattr(package, "patterns")
    assert not (Path(package.__file__).parent / "patterns.py").exists()
    assert not (Path(package.__file__).parent / "extract.py").exists()
