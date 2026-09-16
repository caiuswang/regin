"""The feature population, and the one lane expression (CAI-267).

Two things land here. **A task enters the population when something names it as
parent** — a row that exists only because an operator declared it, exactly like
the three union arms beside it. Merely having run is not enough; that would be
admission by filter, which is an inference about which keys are really
features.

And **the lane moves into one expression**. It was copy-pasted across five
sites, three of which are the ledger page — so without the parent hop a native
feature would show sessions and cost in the index and zero on its own page.
Extraction forces one collapse rule, `LIMIT 1` everywhere, which **changes a
live figure**: `sdk-30da717d448f` has two `agent_run_sessions` rows, so the
index counted 51 where attribution counted 50.
"""

from __future__ import annotations

import pytest
from sqlmodel import text

from lib.features import attribution, cost as cost_module, index as index_module
from lib.features import lane as lane_module
from lib.orm import SessionLocal
from lib.tasks import grain
from lib.tasks.integrations.regin import key_for_task


def _keys() -> set:
    with SessionLocal() as session:
        rows = session.exec(text(f"WITH {lane_module.KEYS} SELECT key FROM keys")).all()
    return {row[0] for row in rows}


# ── the population ───────────────────────────────────────────

def test_a_task_enters_the_population_when_something_names_it_as_parent(
        native_task):
    parent = native_task(title="a feature")
    assert key_for_task(parent) not in _keys()

    grain.set_parent(native_task(), parent)
    assert key_for_task(parent) in _keys()


def test_a_root_task_enters_the_population_when_it_is_run(
        native_task, task_run_row):
    """Run is the declaration: the operator said "build this", and the
    attempt is the feature's first session (regin:76)."""
    task_id = native_task()
    assert key_for_task(task_id) not in _keys()
    task_run_row(task_id, trace_id="t-1")
    assert key_for_task(task_id) in _keys()


def test_every_root_task_is_not_swept_into_the_population(native_task):
    """Admission by filter would be an inference about which keys are really
    features — the thing constraint 1 forbids."""
    for _ in range(3):
        native_task()
    assert _keys() == set()


def test_the_population_is_unchanged_on_todays_database(native_task,
                                                        defect_row, task_lane):
    """+0 keys on landing: the fourth arm reads `parent_id`, and nothing has
    one yet."""
    defect_row("CAI-1")
    task_lane(7, ["CAI-2"], [("t-1", "done")])
    native_task()  # a root task with no children contributes nothing
    assert _keys() == {"CAI-1", "CAI-2"}


def test_a_feature_declared_before_its_first_child_is_invisible(native_task):
    """Stated as intent, not a bug: nothing yet names it as parent."""
    task_id = native_task(title="specced, not started")
    assert key_for_task(task_id) not in _keys()
    # It still resolves, reads its stage and reads its ledger.
    from lib.features import ledger as ledger_module
    assert ledger_module.build(key_for_task(task_id)).key == key_for_task(task_id)


def test_detaching_the_last_child_removes_the_key_and_its_cache_row(
        native_task):
    from lib.tasks import issue_cache

    parent = native_task()
    child = native_task()
    grain.set_parent(child, parent)
    key = key_for_task(parent)
    assert key in _keys()

    grain.set_parent(child, None)
    assert key not in _keys()
    # The cache row is a rendering of a key that no longer names a feature;
    # it is harmless and deliberately left, since the key still resolves.
    assert issue_cache.lookup("regin", key)[0] is not None


# ── the one lane ─────────────────────────────────────────────

def test_all_five_lane_sites_read_one_expression():
    """The lane was copy-pasted five times and had already drifted."""
    import inspect

    from lib.features import attribution as attr, cost, index

    for module in (attr, cost, index):
        source = inspect.getsource(module)
        assert "JOIN agent_task_links l ON l.task_id = r.task_id" not in source, (
            f"{module.__name__} still carries its own copy of the lane")


def test_the_index_and_the_feature_page_count_the_same_sessions_for_one_key(
        native_task, task_run_row, session_row):
    parent = native_task()
    child = native_task(parent_id=parent)
    session_row("conv-1", cost=1.5)
    task_run_row(child, trace_id="conv-1")
    grain.recompute_parent(child)

    key = key_for_task(parent)
    from_index = {row.key: row.sessions for row in index_module.page().items}
    assert from_index[key] == len(attribution.sessions_for_feature(key)) == 1


def test_a_trace_with_two_conversations_counts_once_everywhere(
        native_task, task_run_row, session_row, run_alias):
    """A run driving two conversations is an ingestion artefact, not a fact
    worth preserving. `LIMIT 1` everywhere — this changes a live figure."""
    parent = native_task()
    child = native_task(parent_id=parent)
    session_row("conv-a", cost=2.0)
    session_row("conv-b", cost=3.0)
    task_run_row(child, trace_id="run-1")
    run_alias("run-1", "conv-a")
    run_alias("run-1", "conv-b")
    grain.recompute_parent(child)

    key = key_for_task(parent)
    from_index = {row.key: row.sessions for row in index_module.page().items}
    assert from_index[key] == 1
    assert len(attribution.sessions_for_feature(key)) == 1


def test_a_native_features_sessions_are_its_childrens_runs(
        native_task, task_run_row, session_row):
    parent = native_task()
    first = native_task(parent_id=parent)
    second = native_task(parent_id=parent)
    session_row("conv-1", cost=1.0)
    session_row("conv-2", cost=2.0)
    task_run_row(first, trace_id="conv-1")
    task_run_row(second, trace_id="conv-2")

    key = key_for_task(parent)
    traces = {row.trace_id for row in attribution.sessions_for_feature(key)}
    assert traces == {"conv-1", "conv-2"}


def test_traceless_runs_are_reported_for_a_native_feature(
        native_task, task_run_row):
    """A feature that burned three failed launches must not read as
    untouched."""
    parent = native_task()
    child = native_task(parent_id=parent)
    task_run_row(child, trace_id=None)
    assert attribution.traceless_runs(key_for_task(parent)) == 1


def test_coverage_reads_the_shared_lane(native_task, task_run_row, session_row):
    parent = native_task()
    child = native_task(parent_id=parent)
    session_row("conv-1", cost=4.0)
    session_row("elsewhere", cost=1.0)
    task_run_row(child, trace_id="conv-1")

    coverage = cost_module.coverage()
    assert coverage.declared_sessions == 1
    assert coverage.declared_usd == pytest.approx(4.0)


def test_the_index_page_stays_one_statement(native_task, counted_queries,
                                            task_run_row, session_row):
    parent = native_task()
    child = native_task(parent_id=parent)
    session_row("conv-1", cost=1.0)
    task_run_row(child, trace_id="conv-1")

    with counted_queries() as count:
        page = index_module.page()
    assert page.items
    # The page's own statement, plus `coverage()`'s two. A per-ROW query is
    # what this forbids, and adding a second feature must not move the count.
    baseline = count.value

    other = native_task()
    grain.set_parent(native_task(), other)
    with counted_queries() as again:
        index_module.page()
    assert again.value == baseline


# ── the cache and the ambiguity guard ────────────────────────

def test_a_child_event_writes_the_native_keys_issue_cache_row(
        native_task, task_run_row):
    from lib.tasks import issue_cache

    parent = native_task()
    child = native_task()
    grain.set_parent(child, parent)
    snapshot, synced_at = issue_cache.lookup("regin", key_for_task(parent))
    assert snapshot is not None and synced_at is not None
    assert snapshot["key"] == key_for_task(parent)


def test_the_cache_write_goes_through_resolve_and_fetch_issue(
        native_task, monkeypatch):
    """No new trigger and no provider branch — the generic path."""
    seen = []
    from lib.defects import tracker

    original = tracker.resolve

    def _spy(key):
        seen.append(key)
        return original(key)

    monkeypatch.setattr(tracker, "resolve", _spy)
    parent = native_task()
    grain.set_parent(native_task(), parent)
    assert key_for_task(parent) in seen


def test_synced_at_no_longer_feeds_the_index_ordering():
    """A native key's cache row is rewritten on every child event while a
    Linear key's moves only when a human clicks Refresh — leaving it in would
    quietly make the index "native features, then everything else"."""
    assert "c.synced_at" not in index_module._ORDER
    assert "synced_at" not in index_module._ORDER


def test_a_parent_that_also_holds_a_link_is_ambiguous(native_task, task_lane):
    """One task cannot hold two feature keys. The guard now lives in the lane,
    so the index counts what the ledger counts."""
    parent = native_task()
    grain.set_parent(native_task(), parent)
    with SessionLocal() as session:
        session.exec(text(
            "INSERT INTO agent_task_links (task_id, provider, external_key,"
            " url, created_at) VALUES (:i, 'linear', 'CAI-9', 'https://x',"
            " '2026-09-01 10:00:00')").bindparams(i=parent))
        session.commit()

    with pytest.raises(attribution.AmbiguousTaskLink):
        attribution.sessions_for_feature(key_for_task(parent))
