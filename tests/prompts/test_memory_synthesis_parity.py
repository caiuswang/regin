"""Characterization test: the editable ``memory-reflect-synthesis`` skeleton
renders byte-identical to the pre-refactor hardcoded synthesis prompt.

The prompt is built inline inside ``reflect._llm_synthesis``; a capturing fake
``llm`` records the exact string passed to ``complete``. ``_reference_prompt``
reproduces the old builder — the frozen ``_SYNTHESIS_PROMPT`` literal with
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

_FROZEN_SYNTHESIS_PROMPT = (
    "Several memories from past coding sessions are clustered below because "
    "an embedding model judged them topically related. Write ONE higher-level, "
    "reusable rule that captures the general principle they share — more "
    "abstract than any single entry, yet still concrete and actionable for a "
    "future session. If they share no genuine common principle (merely "
    "surface-similar), reply with exactly NONE.\n\n"
    "Respond with a JSON object and nothing else:\n"
    '  {"title": "the principle in one line, <= 80 chars", '
    '"body": "the reusable rule, <= 600 chars"}\n'
    "or the bare word NONE.\n\n"
    "{entries}"
)


class _CaptureLLM:
    """Records the prompt passed to ``complete`` and declines (NONE)."""

    def __init__(self):
        self.prompt = None

    def complete(self, prompt, **_):
        self.prompt = prompt
        return "NONE"


def _reference_prompt(members):
    entries = "\n\n".join(f"[{i + 1}] {reflect._doc_text(m)[:600]}"
                          for i, m in enumerate(members))
    return _FROZEN_SYNTHESIS_PROMPT.replace("{entries}", entries)


def _run(members):
    llm = _CaptureLLM()
    reflect._llm_synthesis(llm, members)
    return _reference_prompt(members), llm.prompt


_MEMBERS = [
    {"title": "Read before Edit", "body": "Edit fails when the file wasn't Read."},
    {"title": "", "body": "Spans are append-only; dedup happens at read time."},
    {"title": "Use the venv", "body": "Run regin via .venv/bin/python."},
]


def test_parity_empty_members():
    # Edge case: no members — {entries} substitutes to "".
    expected, actual = _run([])
    assert actual == expected


def test_parity_clustered_members():
    expected, actual = _run(_MEMBERS)
    assert actual == expected
    assert "[1] Read before Edit" in actual
    # sanity: the literal JSON single braces survive substitution.
    assert '{"title": "the principle in one line' in actual
