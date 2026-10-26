"""The spec reader takes scenarios from parser tokens, never from line matches."""

from __future__ import annotations

import ast
from pathlib import Path

from lib.specs.reader import read_spec
from tests.spec_fixtures import gherkin, row, spec

SPECS_DIR = Path(__file__).resolve().parents[2] / "lib" / "specs"


def _imports(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module.split(".")[0])
    return found


def test_spec_reader_imports_no_re():
    files = sorted(SPECS_DIR.glob("*.py"))
    assert files, "lib/specs/ holds no modules"
    offenders = [p.name for p in files if "re" in _imports(p)]
    assert offenders == []


def test_nested_fence_scenario_is_not_extracted():
    body = ("## Acceptance\n\n"
            "````markdown\n```gherkin\nScenario: fake — test_fake\n  Then x\n```\n````\n\n"
            "```gherkin\nScenario: real — test_real\n  Then y\n```\n")
    names = [s.test_name for s in read_spec(body).scenarios]
    assert "test_fake" not in names
    assert names == ["test_real"]


def test_docstring_scenario_is_not_extracted():
    body = ('## Acceptance\n\n```gherkin\nScenario: outer — test_outer\n'
            '  Given a doc\n    """\n    Scenario: fake\n    """\n  Then ok\n```\n')
    doc = read_spec(body)
    assert [s.title for s in doc.scenarios] == ["outer — test_outer"]
    assert doc.scenarios[0].line == 4


def test_non_ascii_title_without_test_name_is_refused():
    body = "## 验收\n\n```gherkin\nScenario: 新建对话框用富文本\n  Then 通过\n```\n"
    doc = read_spec(body)
    assert doc.scenarios[0].test_name is None
    problem = next(p for p in doc.problems if p.code == "no_test_name")
    assert problem.subject == "新建对话框用富文本"
    assert problem.line == 4
    assert "test_" in problem.message and "explicit" in problem.message


def test_duplicate_scenario_names_are_refused():
    body = spec(gherkin("Login works", "login: works", "test_other"),
                row("S1", "test_login_works, test_other"))
    dupes = [p for p in read_spec(body).problems if p.code == "duplicate_name"]
    assert [p.subject for p in dupes] == ["test_login_works"]
