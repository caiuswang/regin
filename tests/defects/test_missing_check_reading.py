"""`missing_check` is a claim; this is the reading that redeems it.

Sentry splits what regin conflated: a commit saying `Fixes X` *references* a
fix, and only a release containing it *resolves* one. The analogue here is
that naming the check that would have caught a defect is not writing it — so
the ledger reads the claim against the tests that actually exist and says
which of the three it is.
"""

from __future__ import annotations

from lib.defects import checks, store
from lib.features import ledger as ledger_module


def _tree(tmp_path, body: str = "", *, spec: str = ""):
    """A repo whose two suites are exactly what the test puts in them."""
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["suite"]\n')
    suite = tmp_path / "suite"
    suite.mkdir()
    (suite / "test_things.py").write_text(body)
    frontend = tmp_path / "frontend" / "tests"
    frontend.mkdir(parents=True)
    (tmp_path / "frontend" / "playwright.config.js").write_text(
        "export default { testDir: './tests' }\n")
    (frontend / "rail.spec.js").write_text(spec)
    return tmp_path


def test_a_named_check_that_exists_reads_as_written(tmp_path):
    root = _tree(tmp_path, "def test_the_badge_matches_the_queue():\n    pass\n")

    oracle = checks.read_oracle(root)

    assert checks.reading("test_the_badge_matches_the_queue", oracle) \
        == checks.WRITTEN


def test_a_named_check_nobody_wrote_reads_as_not_yet_written(tmp_path):
    root = _tree(tmp_path, "def test_something_else():\n    pass\n")

    oracle = checks.read_oracle(root)

    assert checks.reading("test_the_badge_matches_the_queue", oracle) \
        == checks.NOT_WRITTEN


def test_prose_that_names_no_test_is_not_a_check(tmp_path):
    """Two of the 48 closed defects said only "fixed". Counting that as a
    named check is exactly how the one field that changes future behaviour
    gets rubber-stamped."""
    oracle = checks.read_oracle(_tree(tmp_path))

    for prose in ("fixed", "a regression test", None, "",
                  "run pytest over the whole tree, not one directory"):
        assert checks.reading(prose, oracle) == checks.NO_CHECK_NAMED, prose


def test_a_node_id_reduces_to_the_test_it_names(tmp_path):
    """`tests/x.py::test_y[case]` is the spelling pytest prints, so it is the
    spelling an operator pastes."""
    root = _tree(tmp_path, "def test_y():\n    pass\n")
    oracle = checks.read_oracle(root)

    assert checks.reading("suite/test_things.py::test_y[case]", oracle) \
        == checks.WRITTEN


def test_an_async_test_is_collected(tmp_path):
    root = _tree(tmp_path, "async def test_awaited():\n    pass\n")

    assert "test_awaited" in checks.read_oracle(root).pytest_names


def test_the_repo_scans_its_own_configured_testpaths():
    """The oracle is not a hardcoded directory: it is whatever `testpaths`
    pytest is configured with, read from `pyproject.toml`."""
    oracle = checks.read_oracle()

    assert "test_the_repo_scans_its_own_configured_testpaths" \
        in oracle.pytest_names
    assert "test_a_prompt_always_posts" in oracle.pytest_names, \
        "hook_manager/tests is a configured testpath too"
    assert "responsive.spec.js" in oracle.spec_files, \
        "playwright's own testDir is the other half of this repo's checks"


def test_the_ledger_flags_a_closed_defect_whose_check_was_never_written(
        feature_row):
    feature = feature_row()
    written = store.record("first", feature_id=feature.id)
    store.close(written.id, missing_check=(
        "test_the_ledger_flags_a_closed_defect_whose_check_was_never_written"))
    absent = store.record("second", feature_id=feature.id)
    store.close(absent.id, missing_check="test_nobody_ever_wrote_this_one")
    unnamed = store.record("third", feature_id=feature.id)
    store.close(unnamed.id, missing_check="fixed")

    report = ledger_module.build(feature.id)

    by_id = {row.defect_id: row.reading for row in report.checks.rows}
    assert by_id[written.id] == checks.WRITTEN
    assert by_id[absent.id] == checks.NOT_WRITTEN
    assert by_id[unnamed.id] == checks.NO_CHECK_NAMED
    assert report.checks.written == 1


def test_an_unredeemed_claim_is_counted_in_a_gap_line_never_as_written(
        feature_row):
    """A figure that states its own gaps is this ledger's whole convention:
    "1 written of 2" with no gap line reads as one defect still open."""
    feature = feature_row()
    row = store.record("it broke", feature_id=feature.id)
    store.close(row.id, missing_check="test_nobody_ever_wrote_this_one")

    report = ledger_module.build(feature.id)
    printed = "\n".join(ledger_module.render(report))

    assert report.checks.to_dict()["gaps"]["check_not_yet_written"] == 1
    assert report.checks.written == 0
    assert "check-not-yet-written 1" in printed
    assert f"#{row.id}  {checks.NOT_WRITTEN}" in printed


def test_an_open_defect_is_not_a_gap_in_the_checks_reading(feature_row):
    """It has named no check because it has not been closed. Counting it here
    would report one bug twice — once as open, once as unredeemed."""
    feature = feature_row()
    store.record("still broken", feature_id=feature.id)

    report = ledger_module.build(feature.id)

    assert report.checks.rows == []
    assert report.open_defects == 1


def test_the_reading_is_served_to_the_browser(flask_client, feature_row):
    """The ledger page is where "how is this going" is asked, so the reading
    has to reach it — a figure only the CLI prints is one the operator reading
    the feature page never sees."""
    from lib.auth import create_token

    feature = feature_row()
    row = store.record("it broke", feature_id=feature.id)
    store.close(row.id, missing_check="test_nobody_ever_wrote_this_one")
    headers = {"Authorization": f"Bearer {create_token(2, 'ed', 'editor')}"}

    body = flask_client.get(f"/api/features/{feature.id}",
                            headers=headers).get_json()

    served = body["defects"]["checks"]
    assert served["gaps"]["check_not_yet_written"] == 1
    assert served["rows"][0]["defect_id"] == row.id
    assert served["rows"][0]["reading"] == checks.NOT_WRITTEN


# ── Playwright is the other suite a check gets named in ──────

SPEC = """import { test } from './auth-fixture.js'
test('the rail keeps its width', async ({ page }) => {})
"""


def test_a_quoted_spec_title_that_exists_reads_as_written(tmp_path):
    """Most of this repo's closed defects name a Playwright title, not a
    pytest function. An oracle that only knew pytest read 20 of them as
    naming no check at all — a false reading, and the loudest one."""
    oracle = checks.read_oracle(_tree(tmp_path, spec=SPEC))

    assert checks.reading("rail.spec.js :: 'the rail keeps its width'",
                          oracle) == checks.WRITTEN


def test_a_quoted_title_nobody_wrote_reads_as_not_yet_written(tmp_path):
    """The title is the more specific claim, so a spec file that exists does
    not redeem a title inside it that does not."""
    oracle = checks.read_oracle(_tree(tmp_path, spec=SPEC))

    assert checks.reading("rail.spec.js :: 'the rail is never clipped'",
                          oracle) == checks.NOT_WRITTEN


NODE_SPEC = """import { test } from './auth-fixture.js'
test('test_the_rail_folds', async ({ page }) => {})
"""


def test_a_spec_node_id_is_a_claim_about_its_playwright_title(tmp_path):
    """Defect #219: `x.spec.js::test_y` read `test_y` as a pytest function,
    found no `def`, and called an existing Playwright test not written."""
    oracle = checks.read_oracle(_tree(tmp_path, spec=NODE_SPEC))

    assert checks.reading(
        "frontend/tests/rail.spec.js::test_the_rail_folds (fixed in abc123)",
        oracle) == checks.WRITTEN
    assert checks.reading("rail.spec.js::test_the_rail_is_clipped",
                          oracle) == checks.NOT_WRITTEN
    assert checks.reading("rail.spec.js::a prose title with no end",
                          oracle) == checks.UNVERIFIED


def test_a_bare_spec_filename_is_the_spelling_playwright_accepts(tmp_path):
    """`playwright test rail.spec.js` is what the operator ran, so it is what
    they paste — a reading that only knew the full path called it missing.
    It stops at `unverified`: the file is the most the claim proves."""
    oracle = checks.read_oracle(_tree(tmp_path, spec=SPEC))

    assert checks.reading("rail.spec.js — the rail under a narrow viewport",
                          oracle) == checks.UNVERIFIED
    assert checks.reading("frontend/tests/rail.spec.js", oracle) \
        == checks.UNVERIFIED
    assert checks.reading("gutter.spec.js — a spec nobody added", oracle) \
        == checks.NOT_WRITTEN


def test_a_pytest_name_outranks_a_spec_file_in_the_same_claim(tmp_path):
    """Only the most specific kind present is judged. Crediting the file
    would pass a check whose own author said which function it had to be."""
    oracle = checks.read_oracle(
        _tree(tmp_path, "def test_present():\n    pass\n", spec=SPEC))

    assert checks.reading("rail.spec.js and test_absent", oracle) \
        == checks.NOT_WRITTEN
    assert checks.reading("rail.spec.js and test_present", oracle) \
        == checks.WRITTEN


def test_a_named_test_module_is_a_file_level_claim(tmp_path):
    """`tests/x/test_web_list_query.py:287-330` names a suite that exists.
    Reading it as a missing *function* reported written checks as gaps;
    reading it as written would stamp a file for a case nobody added."""
    oracle = checks.read_oracle(
        _tree(tmp_path, "def test_present():\n    pass\n"))

    assert checks.reading("suite/test_things.py:287-330", oracle) \
        == checks.UNVERIFIED
    assert checks.reading("suite/test_absent_module.py", oracle) \
        == checks.NOT_WRITTEN


def test_a_node_id_is_a_claim_about_its_function_not_its_module(tmp_path):
    """The rubber stamp this reading exists to stop. A pytest node id names a
    module AND a function; crediting the module reads every
    `tests/x/test_y.py::test_nobody_wrote_this` as written while the function
    does not exist."""
    oracle = checks.read_oracle(
        _tree(tmp_path, "def test_present():\n    pass\n"))

    assert checks.reading("suite/test_things.py::test_absent", oracle) \
        == checks.NOT_WRITTEN
    assert checks.reading("suite/test_things.py::test_present", oracle) \
        == checks.WRITTEN
    # Still a module-level claim when no function is named beside it — and
    # a module is only ever `unverified`.
    assert checks.reading("suite/test_things.py:287-330", oracle) \
        == checks.UNVERIFIED


def test_a_module_stem_never_redeems_a_bare_function_claim(tmp_path):
    """`test_things` spelled without `.py` is a function claim, and there is
    no function of that name — the file next to it is not the check."""
    oracle = checks.read_oracle(
        _tree(tmp_path, "def test_present():\n    pass\n"))

    assert checks.reading("a test_things style unit test", oracle) \
        == checks.NOT_WRITTEN


def test_a_file_that_exists_never_reads_as_a_written_check(tmp_path):
    """The rubber stamp one rung below the module/function split. This row
    says in words that nobody wrote it; reading the file it points at as the
    check is exactly the claim this whole reading refuses to take on trust.
    Measured on the live ledger, file-only redemption stamped 6 of 32."""
    oracle = checks.read_oracle(
        _tree(tmp_path, "def test_present():\n    pass\n", spec=SPEC))

    unwritten = ("The regression belongs in suite/test_things.py "
                 "but nobody ever wrote it.")

    assert checks.reading(unwritten, oracle) == checks.UNVERIFIED
    assert checks.reading(unwritten, oracle) != checks.WRITTEN


def test_a_py_inside_a_quoted_title_is_words_not_a_reference(tmp_path):
    """A quoted run is a spec title — a literal. Reading a `.py` inside one as
    a module claim let the file outrank the very title that contained it."""
    oracle = checks.read_oracle(
        _tree(tmp_path, "def test_present():\n    pass\n", spec=SPEC))

    assert checks.reading(
        "a spec titled 'the row mentions test_things.py inline'",
        oracle) == checks.NOT_WRITTEN


def test_every_named_check_must_be_redeemed_not_merely_one(tmp_path):
    """A row that named two checks and got one is half-redeemed, and half is
    not written — in either order."""
    oracle = checks.read_oracle(
        _tree(tmp_path, "def test_present():\n    pass\n"))

    assert checks.reading("test_present and test_absent", oracle) \
        == checks.NOT_WRITTEN
    assert checks.reading("test_absent and test_present", oracle) \
        == checks.NOT_WRITTEN
    assert checks.reading("test_present alone", oracle) == checks.WRITTEN


def test_the_unverified_rung_is_a_gap_never_a_written_check(feature_row):
    feature = feature_row()
    row = store.record("it broke", feature_id=feature.id)
    store.close(row.id, missing_check="tests/defects/test_fix_item.py:1-20")

    report = ledger_module.build(feature.id)

    assert report.checks.written == 0
    assert report.checks.to_dict()["gaps"]["check_name_unverified"] == 1
    assert checks.UNVERIFIED in "\n".join(ledger_module.render(report))
