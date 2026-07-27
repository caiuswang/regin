"""The question structure carried on an `AskUserQuestion` span.

Shared by both producers on purpose. This is not cosmetic formatting — it is
the interaction contract: the `/live` answer sheet refuses to render options
for an ask whose span has no `questions`, so a producer that omits it emits a
question nobody can answer.
"""

from __future__ import annotations


def ask_option(option: dict) -> dict:
    """One option, keeping every field the terminal itself displays.

    The terminal renders an option as `<label> — <description>` plus previews
    when present, so dropping any of the three would make the trace a
    misleading replay of what the operator actually saw.
    """
    out: dict = {'label': option.get('label')}
    description = option.get('description')
    if description:
        out['description'] = description
    preview = option.get('preview')
    if preview:
        out['preview'] = preview
    return out


def ask_questions(tool_input: dict) -> list[dict]:
    """The questions an ask poses, without answers."""
    out: list[dict] = []
    for question in (tool_input or {}).get('questions') or []:
        if not isinstance(question, dict):
            continue
        out.append({
            'question': question.get('question'),
            'header': question.get('header'),
            'options': [ask_option(o) for o in (question.get('options') or [])],
            'multiSelect': question.get('multiSelect', False),
        })
    return out
