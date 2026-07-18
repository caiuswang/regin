"""Stage 2 surface registrations for the deep-grader (LLM-judge) system prompts.

The two agentic judges keep their built-in system prompts as module constants
(``agentic._PROMPT`` for correctness, ``process_agentic._PROMPT`` for process).
Registering them here as editable *surfaces* makes those bodies seed-able and
editable through the unified prompt-template UI + ``seed_builtin_skeletons``,
without relocating the constants. The ``default_body`` is a **lazy callable**
(imported only when invoked, at seed time) so this module can register at import
without the ``prompts → grader → prompts`` import cycle a top-level import
would create — the same trick ``default_system_prompts()`` uses.

The standalone ``agentic``/``process_agentic`` judges above are no longer the
live deep-tier path — ``lib/grader/combined_agentic.py`` replaced them with one
combined judge call, but kept its own separate copy of the role/rubric text.
The three ``grader-combined-*`` surfaces below register THAT prompt (the one
``service.py`` actually sends), using the same lazy-callable trick.

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

from lib.prompts.registry import register_retired_default, register_surface

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


COMBINED_ROLE_SURFACE_ID = "grader-combined-role"
COMBINED_CORRECTNESS_SURFACE_ID = "grader-combined-correctness"
COMBINED_PROCESS_SURFACE_ID = "grader-combined-process"


def _combined_role_default_body() -> str:
    """The combined judge's shared role/evidence-gathering preamble (lazy)."""
    from lib.grader.combined_agentic import _ROLE

    return _ROLE


def _combined_correctness_default_body() -> str:
    """The combined judge's `<correctness>` rubric block (lazy)."""
    from lib.grader.combined_agentic import _CORRECTNESS_BLOCK

    return _CORRECTNESS_BLOCK


def _combined_process_default_body() -> str:
    """The combined judge's `<process>` rubric block (lazy)."""
    from lib.grader.combined_agentic import _PROCESS_BLOCK

    return _PROCESS_BLOCK


register_surface(
    COMBINED_ROLE_SURFACE_ID,
    label="Deep judge — combined role & evidence-gathering",
    area="grader",
    default_body=_combined_role_default_body,
    description=(
        "Shared preamble (role + how to fetch trace evidence) for the single "
        "combined deep-tier judge run that replaced the standalone "
        "correctness/process judges "
        "(`lib/grader/combined_agentic.py::build_combined_prompt`). "
        + _SINGLE_BRACE_NOTE
    ),
    kind="fragment",
    applies_to=("grader",),
    variables=(),
    tags=("grader", "combined"),
)

# Retired role-body hashes: an un-edited stale seed still hashing to one of
# these is healed to the current default by `seed_builtin_skeletons`, so the
# body change reaches existing installs instead of being pinned to the seed
# (`render_surface` prefers the stored row). A reviewer-edited row never
# matches and is left alone. Append a line each time `_ROLE` changes.
for _sha in (
    # `<role>`-first body, before the leading `# Grade an AI coding-agent
    # session` title that keeps the judge session legible in the session list
    "e3a91aca499b9951fb77311e9ff8dbf08dd932f77305203853a4bb6620058dd9",
):
    register_retired_default(COMBINED_ROLE_SURFACE_ID, sha256=_sha)

register_surface(
    COMBINED_CORRECTNESS_SURFACE_ID,
    label="Deep judge — combined correctness block",
    area="grader",
    default_body=_combined_correctness_default_body,
    description=(
        "The `<correctness>` rubric block spliced into the combined judge "
        "prompt when correctness is one of the requested axes "
        "(`lib/grader/combined_agentic.py::build_combined_prompt`)."
    ),
    kind="fragment",
    applies_to=("grader",),
    tags=("grader", "combined", "correctness"),
)

register_surface(
    COMBINED_PROCESS_SURFACE_ID,
    label="Deep judge — combined process block",
    area="grader",
    default_body=_combined_process_default_body,
    description=(
        "The `<process>` rubric block spliced into the combined judge "
        "prompt when process is one of the requested axes "
        "(`lib/grader/combined_agentic.py::build_combined_prompt`)."
    ),
    kind="fragment",
    applies_to=("grader",),
    tags=("grader", "combined", "process"),
)

__all__ = [
    "CORRECTNESS_SURFACE_ID",
    "PROCESS_SURFACE_ID",
    "COMBINED_ROLE_SURFACE_ID",
    "COMBINED_CORRECTNESS_SURFACE_ID",
    "COMBINED_PROCESS_SURFACE_ID",
]
