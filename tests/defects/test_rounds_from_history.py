"""Acceptance items for CAI-210 — rounds read from declared state transitions.

The counting function is pure: it takes a tracker's recorded state history and
returns a number. No prose is parsed, no clock is consulted, nothing is
inferred. The payload shape is pinned by a verbatim capture of a real API
response rather than by an invented one.
"""

from __future__ import annotations

import json
from pathlib import Path

from lib.defects import rounds

FIXTURE = Path(__file__).parent / "fixtures" / "cai_209_state_history.json"


def _entry(state_type: str, *, ended: str | None) -> dict:
    return {"state": {"name": state_type.title(), "type": state_type},
            "startedAt": "2026-09-13T01:00:00.000Z", "endedAt": ended}


def test_a_never_reopened_feature_is_one_round():
    history = [_entry("backlog", ended="t1"), _entry("started", ended="t2"),
               _entry("completed", ended=None)]

    assert rounds.from_state_history(history) == 1


def test_each_completed_state_that_was_left_counts_a_round():
    history = [_entry("started", ended="t1"),
               _entry("completed", ended="t2"),   # walked back once
               _entry("started", ended="t3"),
               _entry("completed", ended="t4"),   # walked back twice
               _entry("started", ended="t5"),
               _entry("completed", ended=None)]

    assert rounds.from_state_history(history) == 3


def test_a_still_open_completed_state_does_not_count_a_round():
    history = [_entry("completed", ended=None)]

    assert rounds.from_state_history(history) == 1


def test_an_empty_or_unknown_history_is_one_round_not_zero():
    assert rounds.from_state_history([]) == 1
    assert rounds.from_state_history(None) == 1


def test_a_cancelled_state_is_not_a_completion():
    history = [_entry("started", ended="t1"), _entry("canceled", ended="t2"),
               _entry("started", ended=None)]

    assert rounds.from_state_history(history) == 1


def test_rounds_are_counted_from_the_real_captured_payload():
    payload = json.loads(FIXTURE.read_text())

    assert rounds.from_issue(payload) == 2
    # The reason the snapshot alone cannot be trusted: reopening cleared it.
    assert payload["completedAt"] is None


def test_no_module_matches_defect_reports_against_prompt_text():
    """CAI-207's prose matching is gone, and must not come back: rounds are
    read from declared transitions, never inferred from what someone typed."""
    import lib.defects as package  # noqa: PLC0415

    assert not hasattr(package, "patterns")
    assert not (Path(package.__file__).parent / "patterns.py").exists()
    assert not (Path(package.__file__).parent / "extract.py").exists()
