"""R5's last acceptance item: every link a Pipeline row or figure offers lands
on the scope it names.

A dead cross-tab link fails silently — the reader is shown *a* list, just not
the one they asked for — so each test here pins the link to the parameter the
server actually parses, and then reads that parameter back over the wire. The
source assertions are deliberately narrow: they name the key and its value,
never the wording around them.
"""

from __future__ import annotations

import pathlib
import re

from lib.auth import create_token

ROOT = pathlib.Path(__file__).resolve().parents[2]
LINKS = ROOT / "frontend/src/utils/featureLinks.js"
FIGURES = ROOT / "frontend/src/components/features/FeatureFigures.vue"
INDEX_TABLE = ROOT / "frontend/src/components/pipeline/FeatureTrackTable.vue"
EVIDENCE = ROOT / "frontend/src/components/features/FeatureEvidence.vue"
DEFECT_FIX_CELL = ROOT / "frontend/src/components/defects/DefectFixCell.vue"
TASK_PANEL = ROOT / "frontend/src/views/TasksView.vue"
LEDGER_VIEW = ROOT / "frontend/src/views/FeatureLedgerView.vue"
USE_TASKS = ROOT / "frontend/src/composables/useTaskQuery.js"
ROUTER = ROOT / "frontend/src/router.js"


def _headers(role: str = "viewer") -> dict:
    return {"Authorization": f"Bearer {create_token(1, role, role)}"}


def test_feature_figures_defect_link_lands_scoped(
        flask_client, tmp_db, feature_row):
    """The reported dead link: the defects tile named a key the ledger never
    emitted (`l.key`) under a parameter the list never read (`feature`), so it
    silently opened the unscoped All list."""
    figures = FIGURES.read_text()
    assert "l.key" not in figures, "the tile still reads a key the payload lacks"
    assert "query: { feature:" not in figures, (
        "the tile still writes `feature=`, which `/defects` does not parse")

    #: The one spelling, and it is the server's own parameter name.
    assert "feature_id" in LINKS.read_text()
    assert "defectsLink(l.id)" in figures

    from lib.defects import store as defect_store

    mine = feature_row("the one being read")
    other = feature_row("someone else's")
    ours = defect_store.record("ours", feature_id=mine.id).id
    theirs = defect_store.record("theirs", feature_id=other.id).id

    scoped = flask_client.get(f"/api/defects?feature_id={mine.id}",
                              headers=_headers())
    assert scoped.status_code == 200
    ids = [row["id"] for row in scoped.get_json()["items"]]
    assert ids == [ours], f"feature_id={mine.id} did not scope the list"
    assert theirs not in ids

    # The old spelling is not merely a different name for the same thing: it
    # narrows nothing, which is exactly why the defect was invisible.
    unscoped = flask_client.get(f"/api/defects?feature={mine.id}",
                                headers=_headers())
    assert {row["id"] for row in unscoped.get_json()["items"]} == {ours, theirs}


def test_feature_index_links_to_its_tasks(
        flask_client, tmp_db, feature_row, native_task):
    """Features → Tasks existed in the data layer and nowhere on the index."""
    row = feature_row("has steps")
    native_task(title="step one", feature_id=row.id)
    native_task(title="step two", feature_id=row.id)
    native_task(title="somebody else's")

    body = flask_client.get("/api/features", headers=_headers()).get_json()
    listed = {item["id"]: item for item in body["items"]}
    assert listed[row.id]["tasks"] == 2

    #: The count and the link have to describe one population.
    listed_tasks = flask_client.get(f"/api/tasks?feature_id={row.id}",
                                    headers=_headers()).get_json()
    assert len(listed_tasks["items"]) == listed[row.id]["tasks"]

    table = INDEX_TABLE.read_text()
    assert "tasksLink(row.id)" in table
    assert ">Tasks<" in table, "the index offers no task column at all"
    assert "path: '/tasks'" in LINKS.read_text()


def test_index_task_count_matches_the_tasks_the_link_lists(
        flask_client, tmp_db, feature_row, native_task):
    """0, 1, N — and an archived task, which the feed hides and the count
    must therefore hide too."""
    empty = feature_row("no steps")
    one = feature_row("one step")
    many = feature_row("three steps, one filed away")

    native_task(title="only", feature_id=one.id)
    for n in range(3):
        native_task(title=f"step {n}", feature_id=many.id)
    native_task(title="filed away", feature_id=many.id, status="archived")

    body = flask_client.get("/api/features", headers=_headers()).get_json()
    counts = {item["id"]: item["tasks"] for item in body["items"]}
    assert counts[empty.id] == 0
    assert counts[one.id] == 1
    assert counts[many.id] == 3

    for feature_id, expected in counts.items():
        # The feed's own defaults, not a bare request: the Tasks tab always
        # sends `root_only`, and a count that agreed only with a hand-written
        # URL would still disagree with the list the link actually opens.
        # `feature_id` beats `root_only` in `lib/tasks/query.py::_by_grain`,
        # so a feature's runs are on both sides of the comparison.
        listed = flask_client.get(
            f"/api/tasks?feature_id={feature_id}&root_only=1",
            headers=_headers()).get_json()
        assert len(listed["items"]) == expected, (
            f"feature {feature_id}: the column says {expected} and the link "
            f"opens {len(listed['items'])}")


def test_tasks_tab_honours_feature_id_from_url(tmp_db):
    """The link is only as good as the tab that receives it: `feature_id` has
    to ride into the request, and it has to be WATCHED — the tabs are kept
    alive, so a value seeded once at setup would ignore a second feature's
    link.

    The list holds its whole state in `location.search` now, so `feature_id`
    is watched by construction: every axis is a computed over `route.query`,
    and there is no seeded ref left to go stale.
    """
    tasks = USE_TASKS.read_text()
    assert "feature_id: s.feature_id" in tasks, (
        "the feed never sends feature_id, so the link narrows nothing")
    assert re.search(r"const state = computed\(\(\) => \{\s*const q = route\.query",
                     tasks), (
        "the axes are not read off route.query, so a query change is not seen")

    panel = TASK_PANEL.read_text()
    assert 'query.state.value.feature_id' in panel, (
        "the panel never reads the arrival scope, so the banner cannot show it")


def test_arriving_by_the_count_ignores_the_filters_that_would_shrink_it(tmp_db):
    """The count the reader clicked is the WHOLE of that feature's tasks, so a
    status or a project left on from yesterday — both persisted in
    localStorage — would open a list shorter than the number that opened it.
    `regin.tasks.status='archived'` is the inverted case: the index count
    excludes archived rows, and that stored filter would show only them.

    SUPPRESSED, never cleared. Every one of those axes is a saved preference,
    so clearing it would spend the reader's setting to fix a count — and the
    way back out of the scope would not give it back.

    The list's state is its URL now, so the suppression is a rule about what
    a bare axis is RESTORED to rather than a second set of refs shadowing the
    controls: a URL that names an arrival scope restores no narrowing, and
    leaving the scope restores them all.
    """
    tasks = USE_TASKS.read_text()
    assert 'const ARRIVAL = ' in tasks and "'feature_id'" in tasks, (
        "feature_id is not treated as an arrival scope")
    assert re.search(r"const NARROWING = \[[^\]]*'status'[^\]]*'priority'"
                     r"[^\]]*'archived'", tasks), (
        "the axes an arrival scope suppresses are not named")

    body = tasks[tasks.index("function restoreStored()"):]
    body = body[:body.index("\n  }")]
    assert "scoped && NARROWING.includes(axis)" in body, (
        "a scoped arrival still restores the filters that would shrink it")
    # Suppressed at RESTORE time only: nothing writes the stored value away,
    # so leaving the scope gives the reader their filter back.
    for axis in ("status", "priority", "archived"):
        assert f"store('{axis}'" not in body

    # `sort` and `group` are not narrowings — a scoped link has no quarrel
    # with how the same rows are ordered or split.
    for axis in ("sort", "group"):
        assert f"'{axis}'" not in tasks[tasks.index("const NARROWING = "):
                                        tasks.index("const NARROWING = ") + 200]
    # … and touching a control ends its suspension, because a control writes
    # its axis into the URL and the URL is read back as the state.
    # Only `group` is exempt (a `?group=` anchor is not a pick); every
    # narrowing axis is stored the moment its control writes it.
    assert ("if (axis in STORED && (axis !== 'group' || 'group' in next)) "
            "store(axis, value)") in tasks

    # An axis the same link names explicitly still wins, by the one rule the
    # whole surface rests on: what the URL states, the URL states.
    assert "if (route.query[axis] != null) continue" in body


def test_feature_sessions_count_links_to_its_traces(tmp_db):
    """A sessions count with no way through to the sessions.

    The sessions LIST filters in component state rather than in its URL, so
    `/trace/sessions?feature=` would be a second dead link; the evidence list
    on the feature's own page is where the traces are, and every row there is
    already an anchor to its trace.
    """
    links = LINKS.read_text()
    assert "SESSIONS_ANCHOR = 'feature-sessions'" in links
    assert "/features/${featureId}" in links

    assert ':id="SESSIONS_ANCHOR"' in EVIDENCE.read_text(), (
        "the evidence list carries no address for the figure to link to")
    assert "/trace/sessions/${r.trace_id}" in EVIDENCE.read_text()

    figures = FIGURES.read_text()
    assert "to: sessions ? { hash: `#${SESSIONS_ANCHOR}` } : null" in figures, (
        "the sessions figure is still not a link")

    # The index carries no sessions column since feature #110: the ledger
    # page's figure is the one way through.

    # The hash is an address, not a movement: `router-link` preventDefaults
    # the native jump, the router declares no `scrollBehavior`, and the page
    # scrolls inside `.content-scroll` — so a link with nothing but a hash
    # behind it is inert while reading as correct. Both arrivals are wired:
    # the click on the page you are already on, and the load of the link the
    # index offers. `frontend/tests/pipeline-cross-links.spec.js` is what
    # proves the wiring MOVES the page; this only pins that it exists.
    assert "scrollIntoView" in links, "the anchor is never revealed"
    assert "onClick: sessions ? () => nextTick(revealSessions) : null" in figures
    view = LEDGER_VIEW.read_text()
    assert "watch(() => route.hash," in view, (
        "arriving at /features/<id>#feature-sessions lands at the top")
    assert "watch(ledger, revealHash)" in view, (
        "the evidence list does not exist until the ledger has drawn")
    # And once per arrival: `ledger` is reassigned by every refresh, ship and
    # declare here, and an unguarded reveal would drag the page back down
    # after each of them.
    assert "revealedHash === route.hash" in view
    assert "scrollBehavior" not in ROUTER.read_text(), (
        "the router now handles hashes — drop this component's own reveal")


def test_a_zero_count_offers_no_link(tmp_db, feature_row):
    """A link onto an empty scope is a link that lands nowhere: every figure
    is an anchor only while it has rows to answer with."""
    figures = FIGURES.read_text()
    assert "to: sessions ?" in figures
    assert "to: l.defects.total ?" in figures

    table = INDEX_TABLE.read_text()
    for guard in ('v-if="row.tasks"', 'v-if="row.defects.total"'):
        assert guard in table, f"{guard} is missing — a zero would be a link"


def test_defect_fix_item_links_to_the_task(
        flask_client, tmp_db, feature_row, native_task):
    """The fix item was displayed and was not a way through to the task."""
    from lib.defects import store as defect_store

    row = feature_row("shipped with a bug")
    defect = defect_store.record("it clips", feature_id=row.id)
    task_id = native_task(title="the fix", feature_id=row.id)

    defect_store.set_fix_item(defect.id, task_id)

    body = flask_client.get(f"/api/defects/{defect.id}",
                            headers=_headers()).get_json()
    assert str(body["defect"]["fix_task_id"]) == str(task_id)

    assert "/tasks/${id}" in DEFECT_FIX_CELL.read_text()
    assert "path: '/tasks/:id'" in ROUTER.read_text()
