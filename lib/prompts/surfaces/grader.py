"""Stage 2 surface registrations for the deep-grader (LLM-judge) system prompts.

The two agentic judges keep their built-in system prompts as module constants
(``agentic._PROMPT`` for correctness, ``process_agentic._PROMPT`` for process).
Registering them here as editable *surfaces* makes those bodies seed-able and
editable through the unified prompt-template UI + ``seed_builtin_skeletons``,
without relocating the constants. The ``default_body`` is a **lazy callable**
(imported only when invoked, at seed time) so this module can register at import
without the ``prompts → grader → prompts`` import cycle a top-level import
would create — the same trick ``default_system_prompts()`` uses.

Single-brace tokens — IMPORTANT
-------------------------------
The judge bodies contain ``{trace_id}`` and ``{python}`` tokens that the grader
fills itself with ``str.replace()`` inside
``lib.grader.prompts.judge_system_prompt``. Those are **single-brace** tokens,
NOT engine ``{{variable}}`` placeholders, so they are deliberately declared with
NO surface ``variables``: the prompt engine only treats ``{{double_brace}}`` as
a slot and passes ``{trace_id}``/``{python}`` through untouched. The reviewer
``<aspects>`` splice and the token substitution both stay in grader code,
operating on the resolved base body — see ``lib/grader/prompts.py``.
"""

from __future__ import annotations

from lib.prompts.registry import register_surface

CORRECTNESS_SURFACE_ID = "grader-correctness"
PROCESS_SURFACE_ID = "grader-process"

_SINGLE_BRACE_NOTE = (
    "`{trace_id}` and `{python}` are grader-side SINGLE-brace tokens the grader "
    "substitutes itself — they are NOT engine `{{variables}}` and are declared as "
    "none here. The reviewer `<aspects>` block is spliced in by grader code after "
    "this body is resolved."
)


def _correctness_default_body() -> str:
    """The built-in correctness-judge system prompt (lazy — avoids a cycle)."""
    from lib.grader.agentic import _PROMPT

    return _PROMPT


def _process_default_body() -> str:
    """The built-in process-judge system prompt (lazy — avoids a cycle)."""
    from lib.grader.process_agentic import _PROMPT

    return _PROMPT


register_surface(
    CORRECTNESS_SURFACE_ID,
    label="Deep judge — correctness",
    area="grader",
    default_body=_correctness_default_body,
    description=(
        "System prompt for the agentic deep-tier correctness judge "
        "(`lib/grader/agentic.py`). " + _SINGLE_BRACE_NOTE
    ),
    applies_to=("grader",),
    variables=(),
    tags=("grader", "correctness"),
)

register_surface(
    PROCESS_SURFACE_ID,
    label="Deep judge — process",
    area="grader",
    default_body=_process_default_body,
    description=(
        "System prompt for the agentic deep-tier process judge "
        "(`lib/grader/process_agentic.py`). " + _SINGLE_BRACE_NOTE
    ),
    applies_to=("grader",),
    variables=(),
    tags=("grader", "process"),
)

__all__ = ["CORRECTNESS_SURFACE_ID", "PROCESS_SURFACE_ID"]
