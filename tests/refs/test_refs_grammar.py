"""The Python half of the reference grammar against the shared fixture.

`frontend/tests/refs-grammar.spec.js` runs the same fixture through
`utils/refs.js`; the two passing together is what makes the grammars one.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.refs.grammar import (
    canonical_kind, decode_file_key, encode_file_key, format_ref,
    parse_ref_target, parse_refs,
)

FIXTURE = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures"
     / "refs_grammar_cases.json").read_text(encoding="utf-8"))


def test_python_grammar_parses_every_fixture_case():
    cases = FIXTURE["parse"]
    assert len(cases) >= 18

    for case in cases:
        got = [{"ref": ref.ref, "label": ref.label}
               for ref in parse_refs(case["text"])]
        assert got == case["refs"], case["name"]


def test_parsed_offsets_cover_the_reference_text():
    body = "基于[F125 二期](feature:125)继续 `[F1 x](feature:1)`"

    (ref,) = parse_refs(body)

    assert body[ref.start:ref.end] == "[F125 二期](feature:125)"


def test_format_ref_escapes_markup_in_labels():
    for case in FIXTURE["format"]:
        assert format_ref(case["kind"], case["key"], case["title"]) \
            == case["ref"], case

    hostile = "<img src=x onerror=window.__pwned=1> & `code` [x] \\ y"
    written = format_ref("feature", "7", hostile)
    assert written == ("[F7 \\<img src=x onerror=window.__pwned=1\\> \\& "
                       "\\`code\\` \\[x\\] \\\\ y](feature:7)")
    # The escape is lossless: reading the reference back gives the title.
    (ref,) = parse_refs(f"see {written} and `code`")
    assert (ref.ref, ref.label) == ("feature:7", f"F7 {hostile}")

    long = format_ref("task", "3", "word " * 30)
    assert long == "[T3 " + ("word " * 12).rstrip() + "…](task:3)"

    with pytest.raises(ValueError):
        format_ref("feature", "0", "zero is no id")
    with pytest.raises(ValueError):
        format_ref("file", "../etc/passwd")


def test_file_keys_are_canonical_and_never_leave_the_repo():
    for case in FIXTURE["file_keys"]:
        assert encode_file_key(case["path"]) == case["key"], case
        if case["key"] is not None:
            assert decode_file_key(case["key"]) == case["path"], case
    for key in FIXTURE["bad_file_keys"]:
        assert decode_file_key(key) is None, key
        assert parse_ref_target(f"file:{key}") is None, key


def test_kind_aliases_and_ref_targets():
    assert [canonical_kind(name) for name in
            ("f", "t", "d", "s", "m", "lesson", "file", "task", "x", "")] \
        == ["feature", "task", "defect", "session", "memory", "memory",
            "file", "task", None, None]
    assert parse_ref_target("feature:128") == ("feature", "128")
    assert parse_ref_target("feature:128 ") is None
    assert parse_ref_target("lesson:" + "a" * 32) is None
