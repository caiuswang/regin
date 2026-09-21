"""Was the check a closed defect named ever actually written?

`missing_check` is a *claim* — "a test of this name would have caught it" —
and nothing checked whether that test exists. Closing the loop needs one more
reading, and it is derived on read from two things already in hand: the string
on the row, and the checks the repo actually holds. No column, no cache, no
write.

A claim is spelled in whichever suite would have caught the bug, so four
spellings are read, most specific first:

    kind         | spelled as                    | redeemed by
    -------------+-------------------------------+---------------------------
    pytest name  | `test_the_badge_matches`      | a `def test_…` under the
                 |                               | configured `testpaths`
    pytest module| `tests/x/test_web_list.py`    | a `test_….py` file there
    spec title   | `'the rail keeps its width'`  | a `test('…')` title under
                 |                               | playwright's `testDir`
    spec file    | `frontend/tests/nav.spec.js`  | a spec of that name under
                 | (or bare `nav.spec.js`)       | the same `testDir`

Only the most specific kind present is judged, and that ordering is
load-bearing rather than tidy. The ordinary spelling of a claim is the pytest
node id `tests/defects/test_ledger.py::test_a_case_nobody_wrote` — module and
function in one string. Credit the module and every such row reads `check
written` while the function it names does not exist, which is precisely the
rubber stamp this reading exists to stop. So a named function is judged as a
function; the file is only the unit when the row named no function at all.
Same rule one level down: a row naming a spec file and a title inside it is a
claim about the title.

Four readings, because a file-level claim cannot reach `check written` and
the string can name no check at all:

    most specific claim | every one redeemed | reads
    --------------------+--------------------+---------------------------
    none                | —                  | no check named
    function or title   | no                 | check not yet written
    function or title   | yes                | check written
    module or spec file | no                 | check not yet written
    module or spec file | yes                | check file exists,
                        |                    |   name unverified

**Every** named check must be redeemed, not one of them: a row naming two
checks that got one is half-redeemed, and half is not written.

A file-level claim stops at `unverified` on purpose. "The regression belongs
in tests/features/test_ledger.py but nobody ever wrote it" names a file that
exists and a check that does not; reading it as written is the same rubber
stamp one rung down. Measured on the live ledger, file-only redemption was
stamping 6 of 32 written rows.

"None" is the row that says only `fixed`. Prose is not a check, and counting
it as one would let the whole point be rubber-stamped.

The oracle reads the test *sources*, deliberately NOT `pytest --collect-only`
and not a Playwright dry run. Collection is the more faithful oracle and takes
~2.9 s against ~40 ms for the scan, and this reading is computed inside every
read of a feature's ledger; a three-second subprocess there would be paid by a
page load. The cost, stated: a test that only exists after collection — a
hook-built parametrization, a title a helper composes — reads as not written.
And a title quoted beside an apostrophe (`the run controls' line`) can be
sliced wrong, which reads as not-written too. Both errors point the same way
on purpose: this reading is a flag to go and check, and a false "not written"
costs a look while a false "written" is the rubber stamp it exists to stop.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from lib.activity_log import get_activity_logger
from lib.settings import settings

log = get_activity_logger("defects")

NO_CHECK_NAMED = "no check named"
NOT_WRITTEN = "check not yet written"
#: A claim that named only the FILE. The file is there; whether it grew the
#: check is not something this reading can see, and saying `check written`
#: would be the rubber stamp at the file rung instead of the module rung.
UNVERIFIED = "check file exists, name unverified"
WRITTEN = "check written"

#: Node-id spellings (`tests/x.py::test_y`, `test_y[case]`) reduce to the same
#: token, which is what an operator pastes out of a pytest failure line.
#: A function, which is any such token NOT spelled as a file.
_PYTEST_NAME = re.compile(r"\btest_[A-Za-z0-9_]+(?!\.py)\b")
_PYTEST_MODULE = re.compile(r"\btest_[A-Za-z0-9_]+(?=\.py\b)")
_PYTEST_DEF = re.compile(r"^\s*(?:async\s+)?def\s+(test_[A-Za-z0-9_]+)", re.M)
_SPEC_FILE = re.compile(r"[\w./-]+\.spec\.[jt]s")
#: A title is quoted because that is how it is written in the spec and how it
#: is pasted out of a Playwright report. The floor keeps `'a'` in prose from
#: being read as a claim.
_QUOTED = re.compile(r"['\"‘“]([^'\"’”]{8,})"
                     r"['\"’”]")
_SPEC_TITLE = re.compile(r"\btest(?:\.\w+)?\s*\(\s*(['\"`])(.+?)\1", re.S)
_TEST_DIR = re.compile(r"testDir:\s*['\"`]([^'\"`]+)")

_FALLBACK_TESTPATHS = ("tests",)
_FRONTEND = "frontend"


def _pyproject_testpaths(root: Path) -> tuple[str, ...]:
    config = root / "pyproject.toml"
    if not config.is_file():
        return _FALLBACK_TESTPATHS
    data = tomllib.loads(config.read_text(encoding="utf-8"))
    paths = (data.get("tool", {}).get("pytest", {})
             .get("ini_options", {}).get("testpaths"))
    return tuple(paths) if paths else _FALLBACK_TESTPATHS


def _playwright_dirs(root: Path) -> list[Path]:
    """Where Playwright is configured to look, resolved against its config."""
    found = []
    for config in (root / _FRONTEND).glob("playwright.config.*"):
        match = _TEST_DIR.search(config.read_text(encoding="utf-8"))
        if match:
            found.append((config.parent / match.group(1)).resolve())
    return found


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


@dataclass(frozen=True)
class Oracle:
    """The checks this repo actually holds, read once per ledger."""

    pytest_names: frozenset[str]
    pytest_modules: frozenset[str]
    spec_titles: frozenset[str]
    #: Every spec, under both its repo-relative path and its bare filename.
    #: Both spellings are in the ledger because Playwright prints the first
    #: and accepts the second, so both are what the operator had on screen.
    spec_files: frozenset[str]

    def has_file(self, named: str) -> bool:
        return named.lstrip("./") in self.spec_files


def read_oracle(root: Optional[Path] = None) -> Oracle:
    base = Path(root) if root is not None else settings.project_root
    names: set[str] = set()
    modules: set[str] = set()
    for rel in _pyproject_testpaths(base):
        for path in (base / rel).rglob("test_*.py"):
            names.update(_PYTEST_DEF.findall(_read(path)))
            modules.add(path.stem)
    titles: set[str] = set()
    specs: set[str] = set()
    for spec_dir in _playwright_dirs(base):
        for path in spec_dir.rglob("*.spec.[jt]s"):
            titles.update(title for _q, title in
                          _SPEC_TITLE.findall(_read(path)))
            specs.add(path.name)
            specs.add(str(path.relative_to(base)))
    log.read("defect_checks_collected", pytest_names=len(names),
             pytest_modules=len(modules), spec_titles=len(titles),
             spec_files=len(specs))
    return Oracle(pytest_names=frozenset(names),
                  pytest_modules=frozenset(modules),
                  spec_titles=frozenset(titles), spec_files=frozenset(specs))


@dataclass(frozen=True)
class Claims:
    """What a `missing_check` string says it is naming."""

    pytest_names: list[str]
    pytest_modules: list[str]
    spec_titles: list[str]
    spec_files: list[str]


def _unquoted(text: str) -> str:
    """The text with quoted runs blanked out.

    A quoted run is a spec title — a literal, not a reference — so a `.py`
    mentioned inside one is part of the title's words. Reading it as a module
    claim let the file outrank the very title that contained it.
    """
    return _QUOTED.sub(" ", text)


def claims_in(missing_check: Optional[str]) -> Claims:
    if not missing_check:
        return Claims([], [], [], [])
    outside = _unquoted(missing_check)
    return Claims(
        pytest_names=list(dict.fromkeys(_PYTEST_NAME.findall(outside))),
        pytest_modules=list(dict.fromkeys(_PYTEST_MODULE.findall(outside))),
        spec_titles=[t.strip() for t in _QUOTED.findall(missing_check)],
        spec_files=list(dict.fromkeys(_SPEC_FILE.findall(outside))))


def _all_in(named, held) -> bool:
    """Every check the row named, not merely one of them.

    `any` read "test_a and test_b" as redeemed when only `test_a` existed —
    a row that named two checks and got one is half-redeemed, and half is
    not written.
    """
    return all(name in held for name in named)


def reading(missing_check: Optional[str], oracle: Oracle) -> str:
    """Judge the most specific kind of claim the row actually made.

    File-level claims are judged last AND cannot reach `check written`: a
    paragraph mentioning `tests/x/test_ledger.py` proves the suite exists,
    never that it grew the case. The row that says "belongs in
    test_ledger.py but nobody ever wrote it" must not read as written.
    """
    claims = claims_in(missing_check)
    if claims.pytest_names:
        return _verdict(_all_in(claims.pytest_names, oracle.pytest_names))
    if claims.spec_titles:
        return _verdict(_all_in(claims.spec_titles, oracle.spec_titles))
    if claims.pytest_modules:
        return _at_file_rung(
            _all_in(claims.pytest_modules, oracle.pytest_modules))
    if claims.spec_files:
        return _at_file_rung(all(oracle.has_file(f) for f in claims.spec_files))
    return NO_CHECK_NAMED


def _verdict(redeemed: bool) -> str:
    return WRITTEN if redeemed else NOT_WRITTEN


def _at_file_rung(exists: bool) -> str:
    return UNVERIFIED if exists else NOT_WRITTEN


@dataclass(frozen=True)
class CheckRow:
    """One closed defect's claim, and whether it was redeemed."""

    defect_id: int
    missing_check: Optional[str]
    reading: str

    def to_dict(self) -> dict:
        return {"defect_id": self.defect_id,
                "missing_check": self.missing_check,
                "reading": self.reading}


@dataclass(frozen=True)
class CheckCounts:
    """The three readings over a feature's closed defects.

    Open defects are not here at all: they have named no check yet because
    they have not been closed, and counting them as a gap would report the
    same bug twice — once as open, once as unredeemed.
    """

    rows: list[CheckRow]

    @property
    def written(self) -> int:
        return sum(1 for row in self.rows if row.reading == WRITTEN)

    @property
    def not_written(self) -> int:
        return sum(1 for row in self.rows if row.reading == NOT_WRITTEN)

    @property
    def unverified(self) -> int:
        return sum(1 for row in self.rows if row.reading == UNVERIFIED)

    @property
    def unnamed(self) -> int:
        return sum(1 for row in self.rows if row.reading == NO_CHECK_NAMED)

    def to_dict(self) -> dict:
        return {"closed": len(self.rows), "written": self.written,
                "gaps": {"check_not_yet_written": self.not_written,
                         "check_name_unverified": self.unverified,
                         "no_check_named": self.unnamed},
                "rows": [row.to_dict() for row in self.rows]}


def counts_for(defects: Iterable) -> CheckCounts:
    """One scan of the test tree for the whole feature, not one per defect."""
    closed = [row for row in defects if row.status == "closed"]
    if not closed:
        return CheckCounts(rows=[])
    oracle = read_oracle()
    return CheckCounts(rows=[
        CheckRow(defect_id=row.id, missing_check=row.missing_check,
                 reading=reading(row.missing_check, oracle))
        for row in closed])
