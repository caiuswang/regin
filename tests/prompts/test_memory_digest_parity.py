"""Characterization test: the editable ``memory-reflect-digest`` skeleton renders
byte-identical to the pre-refactor hardcoded digest prompt.

The prompt is built inline inside ``reflect._llm_digest``; a capturing fake
``llm`` records the exact string passed to ``complete``. ``_reference_prompt``
reproduces the old builder — the frozen ``_DIGEST_PROMPT`` literal with
``{entries}`` replaced, entries assembled via the same (unchanged, shared)
``_doc_text`` helper. Edit the surface body
(``lib/prompts/surfaces/memory.py``) and this reference together.
"""

from __future__ import annotations

import importlib

# `lib.memory` exports a `reflect` *function* that shadows the submodule of the
# same name, so `import lib.memory.reflect as reflect` binds the function. Load
# the actual module explicitly.
reflect = importlib.import_module("lib.memory.reflect")

_FROZEN_DIGEST_PROMPT = (
    "Below are the most important things learned across past coding sessions "
    "for one project scope, highest-priority first. Write a SINGLE compact "
    "briefing a future session should read first: the durable conventions, "
    "gotchas, and decisions — grouped and deduplicated, concrete and "
    "actionable. Omit anything narrow or one-off. Aim for under 800 "
    "characters.\n\n"
    "Respond with a JSON object and nothing else:\n"
    '  {"title": "<= 80 char heading for this briefing", '
    '"body": "the briefing, <= 1500 chars"}\n'
    "or the bare word NONE if there is no durable, reusable signal.\n\n"
    "{entries}"
)


class _CaptureLLM:
    """Records the prompt passed to ``complete`` and declines (NONE)."""

    def __init__(self):
        self.prompt = None

    def complete(self, prompt, **_):
        self.prompt = prompt
        return "NONE"


def _reference_prompt(sources):
    entries = "\n\n".join(f"[{i + 1}] {reflect._doc_text(m)[:400]}"
                          for i, m in enumerate(sources))
    return _FROZEN_DIGEST_PROMPT.replace("{entries}", entries)


def _run(sources):
    llm = _CaptureLLM()
    reflect._llm_digest(llm, sources)
    return _reference_prompt(sources), llm.prompt


_SOURCES = [
    {"title": "Schema drift", "body": "regin init builds from db/schema.sql, not Alembic."},
    {"title": "", "body": "Settings come off the pydantic singleton."},
]


def test_parity_empty_sources():
    # Edge case: no sources — {entries} substitutes to "".
    expected, actual = _run([])
    assert actual == expected


def test_parity_scope_sources():
    expected, actual = _run(_SOURCES)
    assert actual == expected
    assert "[1] Schema drift" in actual
    # sanity: the literal JSON single braces survive substitution.
    assert '{"title": "<= 80 char heading' in actual
