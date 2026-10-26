"""Which named tests exist in a checkout, read from the files' syntax trees.

Python tests come from `ast` (every `test_…` function, methods included);
JS tests from tree-sitter-javascript, where a `test(…)` / `it(…)` call
whose title starts with a `test_…` word is the test of that name — the
convention the spec's Scenario titles carry into Playwright.

Only *existence* is checked. Whether the test passes is the verify tail's
job; a slice that claims done on a name nobody wrote is what this catches.
"""

from __future__ import annotations

import ast
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from lib.activity_log import get_activity_logger
from lib.specs.names import first_word_name

log = get_activity_logger("tasks")

PY_SUFFIX = ".py"
JS_SUFFIXES = (".spec.js", ".test.js", ".spec.mjs", ".test.mjs")
_JS_CALLEES = ("test", "it")
_JS_MODIFIERS = ("only", "skip", "fixme")
_SKIP_DIRS = {".git", "node_modules", ".venv", "__pycache__"}

_CALL_QUERY = "(call_expression function: (_) @callee arguments: (arguments) @args)"


@dataclass(frozen=True)
class TestLocation:
    path: str
    line: int
    runner: str


TestIndex = dict[str, list[TestLocation]]


def _is_py_test(name: str) -> bool:
    return name.endswith(PY_SUFFIX) and (
        name.startswith("test_") or name.endswith("_test.py"))


def _is_js_test(name: str) -> bool:
    return name.endswith(JS_SUFFIXES)


def _git_files(root: Path) -> Optional[list[str]]:
    try:
        proc = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=root, capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return [p for p in proc.stdout.decode("utf-8", "replace").split("\0") if p]


def _walk_files(root: Path) -> list[str]:
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        rel = Path(dirpath).relative_to(root)
        out.extend(str(rel / name) for name in filenames)
    return out


def _py_tests(source: bytes) -> Iterable[tuple[str, int]]:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name.startswith("test_"):
            yield node.name, node.lineno


_TS = None


def _ts():
    global _TS
    if _TS is None:
        import tree_sitter as ts  # noqa: PLC0415 — optional grammar, loaded once
        import tree_sitter_javascript as tsjs  # noqa: PLC0415
        lang = ts.Language(tsjs.language())
        _TS = (ts.Parser(lang), ts.Query(lang, _CALL_QUERY))
    return _TS


def _text(node, src: bytes) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", "replace")


def _is_test_callee(node, src: bytes) -> bool:
    if node.type == "identifier":
        return _text(node, src) in _JS_CALLEES
    if node.type != "member_expression":
        return False
    obj, prop = node.child_by_field_name("object"), node.child_by_field_name("property")
    return (obj is not None and prop is not None and obj.type == "identifier"
            and _text(obj, src) in _JS_CALLEES and _text(prop, src) in _JS_MODIFIERS)


def _literal_title(args, src: bytes) -> Optional[str]:
    """The first argument as text, when it is a string with no interpolation."""
    first = next((c for c in args.named_children if c.type != "comment"), None)
    if first is None or first.type not in ("string", "template_string"):
        return None
    if any(c.type == "template_substitution" for c in first.named_children):
        return None
    return "".join(_text(c, src) for c in first.named_children
                   if c.type in ("string_fragment", "escape_sequence"))


def _js_tests(source: bytes) -> Iterable[tuple[str, int]]:
    import tree_sitter as ts  # noqa: PLC0415
    parser, query = _ts()
    root = parser.parse(source).root_node
    for _, captures in ts.QueryCursor(query).matches(root):
        callee, args = captures["callee"][0], captures["args"][0]
        if not _is_test_callee(callee, source):
            continue
        title = _literal_title(args, source)
        name = first_word_name(title) if title else None
        if name:
            yield name, callee.start_point[0] + 1


def _read(path: Path) -> Optional[bytes]:
    try:
        return path.read_bytes()
    except OSError:
        return None


def build_test_index(root: str | os.PathLike) -> TestIndex:
    """Every named pytest and JS test under `root`, by test name.

    Files are the ones git lists — tracked or untracked-but-not-ignored, so a
    test written and not yet committed still counts; a directory that is not
    a checkout is walked instead.
    """
    base = Path(root)
    files = _git_files(base)
    if files is None:
        files = _walk_files(base)
    index: TestIndex = {}
    for rel in files:
        name = Path(rel).name
        if _is_py_test(name):
            runner, reader = "pytest", _py_tests
        elif _is_js_test(name):
            runner, reader = "playwright", _js_tests
        else:
            continue
        source = _read(base / rel)
        if source is None:
            continue
        for test, line in reader(source):
            index.setdefault(test, []).append(TestLocation(rel, line, runner))
    log.read("test_index_built", root=str(base), tests=len(index))
    return index
