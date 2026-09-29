"""Characterization of `lib.utils.grit_parser.parse_file`: which `@rule`
header blocks become rule dicts, and when a block is dropped or refused."""

from __future__ import annotations

import pytest

from lib.utils.grit_parser import RuleMetadataError, parse_file


def _header(rule_id: str, triggers: str = "Foo, Bar ,") -> str:
    return (f"// @rule id={rule_id}\n// @rule layer=service\n"
            f"// @rule triggers={triggers}\n// @rule severity=warn\n"
            "// @rule guide=my-guide\n// @rule summary=Do the thing\n")


def test_parse_file_keeps_complete_blocks_only(tmp_path):
    grit = tmp_path / ".grit" / "a.grit"
    grit.parent.mkdir()
    grit.write_text(
        _header("first")
        + "// a plain comment keeps the block\n\n"
        + "pattern first() { `x` }\n"
        + "// @rule id=partial\n// @rule layer=service\n"
        + "pattern partial() { `y` }\n"
        + _header("reset")
        + "private pattern helper() { `z` }\n"
        + "pattern reset() { `w` }\n"
        + _header("second", "Only")
        + "  pattern second ( ) { `v` }\n"
    )

    rules = parse_file(str(grit), str(tmp_path))

    assert rules == [
        {"id": "first", "layer": "service", "triggers": ["Foo", "Bar"],
         "severity": "warn", "guide": "my-guide", "summary": "Do the thing",
         "source_file": ".grit/a.grit"},
        {"id": "second", "layer": "service", "triggers": ["Only"],
         "severity": "warn", "guide": "my-guide", "summary": "Do the thing",
         "source_file": ".grit/a.grit"},
    ]


def test_parse_file_refuses_mismatched_id(tmp_path):
    grit = tmp_path / "b.grit"
    grit.write_text(_header("wanted") + "pattern actual() { `x` }\n")

    with pytest.raises(RuleMetadataError, match="does not match pattern name 'actual'"):
        parse_file(str(grit), str(tmp_path))
