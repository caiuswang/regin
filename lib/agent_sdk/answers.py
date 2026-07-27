"""Build the `answers` map an `AskUserQuestion` tool call expects.

The shape is not invented — it is what Claude Code itself records when a
question is answered in the terminal, verified against session transcripts
under `~/.claude/projects/`:

    answers = {"<full question text>": "<option label>"}

Two details matter and are easy to get wrong. Keys are the **full question
text**, not the short `header` the UI renders and keys its payload by. And a
multi-select answer is a single **comma-joined string**, not a list. Get either
wrong and the tool receives input it cannot match to its own questions.
"""

from __future__ import annotations

MULTI_SEPARATOR = ", "


def _option_label(question: dict, index) -> str:
    options = question.get("options") or []
    if not isinstance(index, int) or index < 0 or index >= len(options):
        return ""
    option = options[index]
    return (option.get("label") or "") if isinstance(option, dict) else ""


def _answer_value(question: dict, answer: dict) -> str:
    """One question's answer as the tool wants it.

    `text` wins over a label: the operator either typed a free-form answer or
    annotated a picked option, and in both cases the typed text is the fuller
    statement of intent.
    """
    text = answer.get("text")
    if isinstance(text, str) and text.strip():
        return text.strip()
    label = answer.get("label")
    if isinstance(label, str) and label.strip():
        return label.strip()
    index = answer.get("option_index")
    if isinstance(index, list):
        labels = [_option_label(question, i) for i in index]
        return MULTI_SEPARATOR.join(x for x in labels if x)
    return _option_label(question, index)


def build_answers(questions: list, answers: list) -> dict:
    """`{question text: answer}` for the questions the operator answered.

    Positional: `answers[i]` answers `questions[i]`, which is the contract the
    /live sheet already sends. Unanswered or unresolvable entries are omitted
    rather than sent blank — an empty string reads as a deliberate empty answer.
    """
    out: dict = {}
    for index, question in enumerate(questions or []):
        if not isinstance(question, dict) or index >= len(answers or []):
            continue
        answer = answers[index]
        if not isinstance(answer, dict):
            continue
        text = (question.get("question") or "").strip()
        value = _answer_value(question, answer)
        if text and value:
            out[text] = value
    return out


def build_updated_input(tool_input: dict, answers: list) -> dict:
    """The `updated_input` handed back to the SDK's permission callback.

    The original input is preserved and `answers` added, so the tool still sees
    its full schema — only now with the operator's choices filled in.
    """
    questions = (tool_input or {}).get("questions") or []
    return {**(tool_input or {}), "answers": build_answers(questions, answers)}
