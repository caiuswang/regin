"""Characterization of `_first_checker_name`: the checker the bundle
runner dry-run exercises is the first non-blank `checker:` found while
walking the rule files, skipping unreadable / unparseable files."""

from __future__ import annotations

from types import SimpleNamespace

from lib.rule_engines.manifest import _first_checker_name


def _write(root, rel, text):
    path = root / "rules" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_first_checker_name_cases(tmp_path):
    manifest = SimpleNamespace(rules_dir="rules")
    empty, broken, listed, single = (tmp_path / n for n in ("e", "b", "l", "s"))
    empty.mkdir()
    _write(broken, "a.yaml", "checker: [unclosed\n")
    _write(broken, "b.json", '{"checker": "  "}')
    _write(listed, "a.yaml", "- checker: ''\n- 3\n- checker: ' from_list '\n")
    _write(single, "a.json", '{"checker": "json_checker"}')
    _write(single, "b.yaml", "checker: yaml_checker\n")

    got = [_first_checker_name(root, manifest) for root in (empty, broken, listed, single)]
    assert got == [None, None, "from_list", "json_checker"]
