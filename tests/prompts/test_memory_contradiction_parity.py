"""Characterization test: the editable ``memory-reflect-contradiction`` skeleton
renders byte-identical to the reference builder for
``lib/memory/reflect.py::_llm_pair_verdict``.

A capturing fake ``llm`` records the exact string passed to ``complete``;
``_reference_prompt`` reproduces the 3-way judge prompt — the static
instruction plus timestamped ``A:``/``B:`` lines with each memory's
``_doc_text(...)[:1500]`` interpolated, via the same (unchanged, shared)
``_doc_text`` helper. Edit the surface body
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
        "Two memory entries from past coding sessions follow, each with the "
        "time it was recorded. A is the OLDER entry, B the NEWER. Answer with "
        "exactly one word:\n"
        "- CONTRADICT: they make incompatible claims about the same thing.\n"
        "- OBSOLETE: B describes a later change, fix, or removal that "
        "supersedes what A records.\n"
        "- DISTINCT: neither — they can both stand.\n\n"
        f"A (recorded {a.get('created_at') or 'unknown'}): "
        f"{reflect._doc_text(a)[:1500]}\n\n"
        f"B (recorded {b.get('created_at') or 'unknown'}): "
        f"{reflect._doc_text(b)[:1500]}\n"
    )


_A = {"title": "Read before Edit", "body": "Edit fails when the file wasn't Read.",
      "created_at": "2026-01-01T09:00:00"}
_B = {"title": "Edit needs no Read", "body": "Edit works on any path directly.",
      "created_at": "2026-06-01T09:00:00"}


def test_contradiction_surface_renders_byte_identical():
    llm = _CaptureLLM()
    reflect._llm_pair_verdict(llm, _A, _B)
    assert llm.prompt == _reference_prompt(_A, _B)


def test_contradiction_surface_clips_long_bodies():
    long_a = {"title": "x", "body": "y" * 5000, "created_at": "2026-01-01T09:00:00"}
    long_b = {"title": "z", "body": "w" * 5000, "created_at": "2026-06-01T09:00:00"}
    llm = _CaptureLLM()
    reflect._llm_pair_verdict(llm, long_a, long_b)
    assert llm.prompt == _reference_prompt(long_a, long_b)
    # The call site clips each doc_text to 1500 chars, so a 2×5000-char input
    # cannot leak in full — the rendered prompt stays bounded well under it.
    assert len(llm.prompt) < 3600 < len(long_a["body"]) + len(long_b["body"])


def test_pair_verdict_parses_the_three_answers():
    class _Fixed:
        def __init__(self, answer):
            self._answer = answer

        def complete(self, prompt, **_):
            return self._answer

    for answer, expected in (("CONTRADICT", "CONTRADICT"),
                             ("obsolete", "OBSOLETE"),
                             ("DISTINCT", "DISTINCT"),
                             ("no idea", None),
                             ("", None)):
        assert reflect._llm_pair_verdict(_Fixed(answer), _A, _B) == expected
