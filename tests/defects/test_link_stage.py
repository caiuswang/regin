"""Acceptance items for CAI-216 — the pipeline stage on a serialized task link.

Stage is computed when the link is read, never written into the snapshot: two
of its three inputs live in the snapshot, but the open-defect count changes
independently of any sync.
"""

from __future__ import annotations

import json

from sqlmodel import text

from lib.defects import store
from lib.orm import SessionLocal
from lib.orm.models.tasks import AgentTaskLink
from lib.tasks import links


def _snapshot(state_category: str, *, rounds: int = 1, key: str = "CAI-216"):
    return json.dumps({
        "provider": "linear", "key": key, "url": "https://x/", "title": "t",
        "state": state_category.title(), "state_category": state_category,
        "rounds": rounds, "labels": [], "extra": {},
    })


def _link(snapshot, *, synced="2026-09-13 00:00:00") -> AgentTaskLink:
    """The link, plus the cached issue it reads through.

    The snapshot moved to `issue_cache` — keyed by the feature rather than by
    this row — so putting one in place is a write to that table, not a field
    on the link.
    """
    with SessionLocal() as session:
        session.exec(text(
            "DELETE FROM issue_cache WHERE external_key = 'CAI-216'"))
        if snapshot is not None:
            session.exec(text(
                "INSERT INTO issue_cache (provider, external_key,"
                " snapshot_json, synced_at) VALUES ('linear', 'CAI-216',"
                " :s, :t)").bindparams(s=snapshot, t=synced))
        session.commit()
    return AgentTaskLink(id=1, task_id=1, provider="linear",
                         external_key="CAI-216", url="https://x/")


def test_the_serialized_link_carries_a_stage():
    row = links.serialize(_link(_snapshot("started")))

    assert row["stage"]["stage"] == "building"
    assert row["stage"]["rounds"] == 1


def test_a_link_whose_snapshot_never_synced_has_no_stage():
    """`spec` is a real stage — it must not double as "we have not looked"."""
    row = links.serialize(_link(None, synced=None))

    assert row["stage"] is None


def test_a_reopened_issue_serializes_from_its_current_state():
    """`rounds > 1` no longer names a stage — the pipeline forbids reopening a
    shipped issue, so the reading that fired on it could never fire again."""
    row = links.serialize(_link(_snapshot("started", rounds=2)))

    assert row["stage"]["stage"] == "building"
    assert row["stage"]["rounds"] == 2


def test_the_stage_counts_open_defects_from_the_local_ledger():
    store.record("one that is still open", feature_key="CAI-216")

    row = links.serialize(_link(_snapshot("completed")))

    assert row["stage"]["stage"] == "shipped"
    assert row["stage"]["open_defects"] == 1


def test_closing_a_defect_lowers_the_open_count_without_resyncing():
    """The count is not in the snapshot, so it must not need one to be right."""
    defect = store.record("fixed since", feature_key="CAI-216")
    link = _link(_snapshot("completed"))
    assert links.serialize(link)["stage"]["open_defects"] == 1

    store.close(defect.id, missing_check="a regression test")

    assert links.serialize(link)["stage"]["open_defects"] == 0
