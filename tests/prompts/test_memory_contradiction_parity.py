"""Characterization test: the editable ``memory-reflect-contradiction`` skeleton
renders byte-identical to the pre-refactor inline f-string in
``lib/memory/reflect.py::_llm_says_contradiction``.

A capturing fake ``llm`` records the exact string passed to ``complete``;
``_reference_prompt`` reproduces the old builder — the static instruction plus
``A:``/``B:`` lines with each memory's ``_doc_text(...)[:1500]`` interpolated,
via the same (unchanged, shared) ``_doc_text`` helper. Edit the surface body
(``lib/prompts/surfaces/memory.py``) and this reference together.
"""

from __future__ import annotations

import importlib

# `lib.memory` exports a `reflect` *function* that shadows the submodule of the
# same name; load the actual module explicitly.
reflect = importlib.import_module("lib.memory.reflect")


class _CaptureLLM:
    """Records the prompt passed to ``complete`` and answers DISTINCT."""

    def __init__(self):
        self.prompt = None

    def complete(self, prompt, **_):
        self.prompt = prompt
        return "DISTINCT"


def _reference_prompt(a, b):
    return (
        "Two memory entries from past coding sessions follow. Answer with "
        "exactly one word — CONTRADICT if they make incompatible claims "
        "about the same thing, or DISTINCT otherwise.\n\n"
        f"A: {reflect._doc_text(a)[:1500]}\n\nB: {reflect._doc_text(b)[:1500]}\n"
    )


_A = {"title": "Read before Edit", "body": "Edit fails when the file wasn't Read."}
_B = {"title": "Edit needs no Read", "body": "Edit works on any path directly."}


def test_contradiction_surface_renders_byte_identical():
    llm = _CaptureLLM()
    reflect._llm_says_contradiction(llm, _A, _B)
    assert llm.prompt == _reference_prompt(_A, _B)


def test_contradiction_surface_clips_long_bodies():
    long_a = {"title": "x", "body": "y" * 5000}
    long_b = {"title": "z", "body": "w" * 5000}
    llm = _CaptureLLM()
    reflect._llm_says_contradiction(llm, long_a, long_b)
    assert llm.prompt == _reference_prompt(long_a, long_b)
    # The call site clips each doc_text to 1500 chars, so a 2×5000-char input
    # cannot leak in full — the rendered prompt stays bounded well under it.
    assert len(llm.prompt) < 3300 < len(long_a["body"]) + len(long_b["body"])
