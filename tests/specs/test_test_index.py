"""The test index finds named tests by syntax tree, in Python and JS."""

from __future__ import annotations

import subprocess

from lib.specs.test_index import build_test_index


def _git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def test_test_index_finds_pytest_and_playwright_tests(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_mod.py").write_text(
        "def test_a():\n    pass\n\n\nclass TestX:\n    def test_b(self):\n        pass\n")
    (tmp_path / "e2e").mkdir()
    (tmp_path / "e2e" / "flow.spec.js").write_text(
        "import { test } from '@playwright/test'\n"
        "test('test_c 描述', async () => {})\n"
        "test.skip(`test_d`, async () => {})\n"
        "test(`test_e ${x}`, async () => {})\n"
        "describe('test_f', () => {})\n")
    (tmp_path / "ignored").mkdir()
    (tmp_path / "ignored" / "test_skip.py").write_text("def test_g():\n    pass\n")
    (tmp_path / ".gitignore").write_text("ignored/\n")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "tests", ".gitignore")

    index = build_test_index(tmp_path)

    assert [(loc.path, loc.line, loc.runner) for loc in index["test_a"]] == [
        ("tests/test_mod.py", 1, "pytest")]
    assert [(loc.path, loc.line) for loc in index["test_b"]] == [
        ("tests/test_mod.py", 6)]
    assert [(loc.path, loc.line, loc.runner) for loc in index["test_c"]] == [
        ("e2e/flow.spec.js", 2, "playwright")]
    assert [loc.line for loc in index["test_d"]] == [3]
    for absent in ("test_e", "test_f", "test_g"):
        assert absent not in index
