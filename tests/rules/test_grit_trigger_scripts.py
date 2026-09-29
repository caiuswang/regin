"""Pins the stdout/exit-code contract of the standalone grit trigger scripts
(`filter_grit_output.py`, `find_applicable_files.py`) and their deployed
copies under example/rule/, which must behave identically."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_EXAMPLE_SCRIPTS = ROOT / 'example' / 'rule' / 'regin-python-conventions' / 'scripts'
_SCRIPT_DIRS = [ROOT / 'scripts', _EXAMPLE_SCRIPTS]

_RULES = {'rules': [
    {'id': 'r1', 'triggers': ['*Controller.java', '@RestController']},
    {'id': 'r2', 'triggers': ['Service']},
    {'id': 'r3', 'triggers': []},
    {'id': 'r4', 'language': 'py', 'triggers': ['x.java']},
]}

_FILES = {
    'src/FooController.java': '@RestController\nclass FooController {}',
    'src/BarController.java': 'class BarController {}',
    'src/Other.java': '@RestController class X {}',
    'src/notes.txt': '@RestController',
    '.hidden/HidController.java': '@RestController',
    'target/TController.java': '@RestController',
    'node_modules/NController.java': '@RestController',
}

_GRIT = (
    'Log in src/FooController.java: bad\n  detail1\n'
    'Log in src/BarController.java: bad\n  detail2\n'
    'Log in src/Missing.java: bad\n'
    'Processed 3 files and found 3 matches\ntrailing\n'
)


@pytest.fixture
def workspace(tmp_path):
    repo = tmp_path / 'repo'
    for rel, text in _FILES.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    rules = tmp_path / 'rules.json'
    rules.write_text(json.dumps(_RULES))
    return rules, repo


def _run(script: Path, args: list[str], stdin: str = '') -> tuple[str, str, int]:
    proc = subprocess.run([sys.executable, str(script), *args], input=stdin,
                          capture_output=True, text=True, check=False)
    return proc.stdout, proc.stderr, proc.returncode


@pytest.mark.parametrize('script_dir', _SCRIPT_DIRS, ids=['scripts', 'example'])
@pytest.mark.parametrize('rule_id,stdin,expected', [
    ('r1', _GRIT,
     'Log in src/FooController.java: bad\n  detail1\n'
     'Processed 3 files and found 1 matches\n'),
    ('r2', _GRIT, 'Processed 3 files and found 0 matches\n'),
    ('r3', _GRIT, 'Processed 3 files and found 0 matches\n'),
    ('nope', _GRIT, _GRIT),
    ('r1', 'Log in src/FooController.java: bad\n',
     'Log in src/FooController.java: bad\n\n'),
    ('r1', 'Log in src/BarController.java: bad\n',
     'Processed 0 files and found 0 matches\n'),
    ('r1', 'Processed 1 file and found 1 matches\n',
     'Processed 1 files and found 0 matches\n'),
    ('r1', 'Processed 1 file and found 1 match\n',
     'Processed 0 files and found 0 matches\n'),
    ('r1', '', 'Processed 0 files and found 0 matches\n'),
])
def test_filter_grit_output(workspace, script_dir, rule_id, stdin, expected):
    rules, repo = workspace
    out, _err, code = _run(script_dir / 'filter_grit_output.py',
                           [str(rules), rule_id, str(repo)], stdin)
    assert (out, code) == (expected, 0)


@pytest.mark.parametrize('script_dir', _SCRIPT_DIRS, ids=['scripts', 'example'])
@pytest.mark.parametrize('rule_id,expected_out,expected_code', [
    ('r1', 'src/FooController.java\n', 0),
    ('r2', '', 1),
    ('r3', '', 1),
    ('r4', '', 1),
    ('nope', '', 2),
])
def test_find_applicable_files(workspace, script_dir, rule_id, expected_out, expected_code):
    rules, repo = workspace
    out, _err, code = _run(script_dir / 'find_applicable_files.py',
                           [str(rules), str(repo), rule_id])
    assert (out, code) == (expected_out, expected_code)


@pytest.mark.parametrize('script', [
    d / name for d in _SCRIPT_DIRS
    for name in ('filter_grit_output.py', 'find_applicable_files.py')
])
def test_wrong_arg_count_is_usage_error(script):
    out, err, code = _run(script, ['only-one'])
    assert (out, code, err.startswith('Usage: ')) == ('', 2, True)
