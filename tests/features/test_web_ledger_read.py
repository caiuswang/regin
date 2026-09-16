"""A web ledger read never touches the tracker (CAI-245).

The rule is not "avoid the network when convenient" — it is that the read is
local, full stop. A fetch a page fires on mount is still a read, and a reloaded
page is the poll `lib/tasks/links.py` refused. So `build()` takes its stage
from a resolver, and the endpoint's resolver reads the cache.
"""

from __future__ import annotations

from lib.features import ledger as ledger_module
from lib.features import stage_source


def _forbidden(*args, **kwargs):
    raise AssertionError("a web ledger read must not reach the tracker")


def test_ledger_build_takes_a_stage_resolver(session_row, task_lane):
    session_row("t-res")
    task_lane(901, ["CAI-800"], [("t-res", "done")])
    from lib.defects.stage import Stage

    report = ledger_module.build(
        "CAI-800", stage_resolver=lambda key: Stage(
            name="shipped", rounds=2, open_defects=0, key=key))

    assert report.stage.name == "shipped"
    assert report.stage.rounds == 2


def test_the_default_resolver_is_still_live(session_row, task_lane,
                                            tracker_issue):
    """`regin feature ledger` must stay behaviourally unchanged: the CLI keeps
    going to the tracker, because a stage read from a stale snapshot would
    change the escape counts in its own report."""
    session_row("t-live")
    task_lane(902, ["CAI-801"], [("t-live", "done")])
    tracker_issue("CAI-801", state_category="completed")

    assert ledger_module.build("CAI-801").stage.name == "shipped"


def test_the_cached_resolver_never_calls_fetch_issue(
        session_row, task_lane, cached_issue, monkeypatch):
    import lib.defects.tracker as tracker_mod
    monkeypatch.setattr(tracker_mod, "resolve", _forbidden)
    session_row("t-cached")
    task_lane(903, ["CAI-802"], [("t-cached", "done")])
    cached_issue("CAI-802", state_category="completed")

    report = ledger_module.build("CAI-802",
                                 stage_resolver=stage_source.from_cache)

    assert report.stage.name == "shipped"


def test_an_unfetched_key_reads_as_no_stage_not_as_spec(
        session_row, task_lane, monkeypatch):
    """`spec` is a real stage. Letting it stand in for "nobody has looked"
    would report a feature as specified-not-started when in fact the cache is
    simply empty."""
    import lib.defects.tracker as tracker_mod
    monkeypatch.setattr(tracker_mod, "resolve", _forbidden)
    session_row("t-nocache")
    task_lane(904, ["CAI-803"], [("t-nocache", "done")])

    report = ledger_module.build("CAI-803",
                                 stage_resolver=stage_source.from_cache)

    assert report.stage is None


def test_the_cached_resolver_reports_freshness_beside_the_stage(cached_issue):
    cached_issue("CAI-804", synced_at="2026-09-02 08:00:00")

    assert stage_source.synced_at("CAI-804") == "2026-09-02 08:00:00"


def test_an_unfetched_key_has_no_freshness():
    assert stage_source.synced_at("CAI-805") is None


def test_a_failed_fetch_does_not_persist_freshness(no_tracker):
    """Freshness is two states — never fetched, or fetched at T. A row meaning
    "tried and failed" is a third that no reader could act on."""
    stage_source.refresh("CAI-806")

    assert stage_source.synced_at("CAI-806") is None


def test_every_successful_fetch_warms_the_cache(tracker_issue):
    """A CLI run warms the web, so the first person to open a feature page is
    not the one who has to press Refresh."""
    tracker_issue("CAI-807", state_category="completed", title="Warmed")
    from lib.defects import stage as stage_module

    stage_module.stage_for_key("CAI-807")

    assert stage_source.snapshot("CAI-807")["title"] == "Warmed"


def test_the_cli_never_reads_the_cache(session_row, task_lane, cached_issue,
                                       tracker_down):
    """The cache feeds display, never a decision. `regin feature ledger` keeps
    exiting 0 with no stage rather than quoting a snapshot it did not just
    verify."""
    session_row("t-cli")
    task_lane(905, ["CAI-808"], [("t-cli", "done")])
    cached_issue("CAI-808", state_category="completed")
    tracker_down()

    assert ledger_module.build("CAI-808").stage is None


def test_no_web_read_calls_fix_states(session_row, task_lane, monkeypatch):
    """`store.fix_states()` does network I/O per fix item — not behind a flag,
    not on demand."""
    from lib.defects import store
    monkeypatch.setattr(store, "fix_states", _forbidden)
    session_row("t-fix")
    task_lane(906, ["CAI-809"], [("t-fix", "done")])

    ledger_module.build("CAI-809", stage_resolver=stage_source.from_cache)


def test_a_task_lane_row_carries_the_task_that_put_it_there(
        session_row, task_lane, no_tracker):
    """The provenance tag links to `/tasks/N`, so the row has to name N.

    Asserted on a ledger `build()` actually assembled rather than on a payload
    a test wrote: the field was missing from `SessionRow` for exactly as long
    as every check for it supplied its own value.
    """
    session_row("t-tagged-row")
    task_lane(910, ["CAI-820"], [("t-tagged-row", "done")])

    row = ledger_module.build("CAI-820").rows[0]

    assert row.provenance == "task-link"
    assert row.task_id == 910
    assert row.to_dict()["task_id"] == 910


def test_a_declared_row_carries_no_task_id(session_row, no_tracker):
    """A tag that names a task nobody linked is a provenance nobody can
    audit."""
    from lib.features import attribution
    session_row("t-plain-row")
    attribution.attribute("CAI-821", "t-plain-row")

    assert ledger_module.build("CAI-821").rows[0].to_dict()["task_id"] is None
