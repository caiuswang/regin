"""The `answers` map handed back to `AskUserQuestion` (`lib/agent_sdk/answers`).

The shape under test is not a regin invention — it is what Claude Code records
in its own transcript when a question is answered in the terminal. These tests
pin the two properties that are easy to get wrong and silently break the tool:
keys are the FULL question text (not the short `header` the sheet keys its
payload by), and a multi-select answer is one comma-joined string, not a list.
"""

from __future__ import annotations

from lib.agent_sdk.answers import build_answers, build_updated_input


def _question(text: str, header: str, *labels: str, multi: bool = False) -> dict:
    return {
        "question": text,
        "header": header,
        "multiSelect": multi,
        "options": [{"label": x} for x in labels],
    }


def test_answers_are_keyed_by_full_question_text_not_header():
    questions = [_question("Which cap should recall use?", "Snippet cap",
                           "600 chars", "900 chars")]

    out = build_answers(questions, [{"option_index": 0, "label": "600 chars"}])

    assert out == {"Which cap should recall use?": "600 chars"}
    assert "Snippet cap" not in out


def test_multi_select_joins_labels_with_comma_space():
    questions = [_question("Which levers?", "Scope", "A", "B", "C", multi=True)]

    out = build_answers(questions, [{"option_index": [0, 2]}])

    assert out == {"Which levers?": "A, C"}


def test_option_index_resolves_to_label_when_sheet_sends_no_label():
    questions = [_question("Pick one?", "P", "First", "Second")]

    out = build_answers(questions, [{"option_index": 1}])

    assert out == {"Pick one?": "Second"}


def test_typed_text_wins_over_label():
    questions = [_question("Pick one?", "P", "First")]

    out = build_answers(questions, [{"option_index": 0, "label": "First",
                                     "text": "something else entirely"}])

    assert out == {"Pick one?": "something else entirely"}


def test_unanswered_question_is_omitted_not_blank():
    questions = [_question("Answered?", "A", "yes"),
                 _question("Skipped?", "B", "no")]

    out = build_answers(questions, [{"option_index": 0, "label": "yes"}])

    assert out == {"Answered?": "yes"}


def test_out_of_range_option_index_is_dropped():
    questions = [_question("Pick one?", "P", "only")]

    assert build_answers(questions, [{"option_index": 7}]) == {}


def test_updated_input_preserves_the_original_tool_schema():
    tool_input = {
        "questions": [_question("Pick one?", "P", "A", "B")],
        "someOtherField": "kept",
    }

    out = build_updated_input(tool_input, [{"option_index": 1, "label": "B"}])

    assert out["someOtherField"] == "kept"
    assert out["questions"] == tool_input["questions"]
    assert out["answers"] == {"Pick one?": "B"}
