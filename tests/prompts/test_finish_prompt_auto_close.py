"""F180 #8 — the finish block tells the agent what Auto close (`one_shot`)
actually does to its session, and that a report is refused while an operator
message is still queued."""

from __future__ import annotations

from lib.tasks import runs

_OLD = "your session stays open either way"


def test_finish_prompt_states_auto_close_truthfully(tmp_db):
    closes = runs._prompt({"id": 3, "title": "t", "body": "b",
                           "feature_id": None}, 7, {"one_shot": True})
    stays = runs._prompt({"id": 3, "title": "t", "body": "b",
                          "feature_id": None}, 7, {"one_shot": False})

    assert _OLD not in closes and _OLD not in stays
    assert "your session closes at the end of the turn" in closes
    assert "your session stays open after it" in stays
    for prompt in (closes, stays):
        assert ("While an operator message is still queued for you, the "
                "command is refused") in prompt
