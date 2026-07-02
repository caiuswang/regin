"""Characterization test: the editable ``memory-expand`` skeleton renders
byte-identical to the pre-refactor hardcoded recall query-expansion prompt.

``_reference_build_prompt`` reproduces the old ``expand._build_prompt`` — the
frozen ``_INSTRUCTION`` literal joined to the request. Only the migrated surface
body + the new ``_build_prompt`` wiring is under test. Edit the surface body
(``lib/prompts/surfaces/memory.py``) and this reference together.
"""

from __future__ import annotations

import lib.memory.expand as expand

_FROZEN_INSTRUCTION = (
    "You rewrite a terse coding-session request into a short, keyword-rich "
    "search query for retrieving relevant past engineering lessons. Expand "
    "abbreviations, name the likely technical subsystems, concepts, and "
    "failure modes the request implies. Preserve the original intent; do "
    "not answer the request or invent specifics not implied by it. Output "
    "ONLY the expanded query as 1-2 sentences, no preamble or quoting."
)


def _reference_build_prompt(query):
    return f"{_FROZEN_INSTRUCTION}\n\nRequest: {query}"


def _run(query):
    return _reference_build_prompt(query), expand._build_prompt(query)


def test_parity_empty_query():
    # Edge case: empty query still renders the instruction + a bare Request line.
    expected, actual = _run("")
    assert actual == expected


def test_parity_typical_query():
    expected, actual = _run("does those config are configable in WebUI?")
    assert actual == expected
    assert actual.endswith("Request: does those config are configable in WebUI?")
