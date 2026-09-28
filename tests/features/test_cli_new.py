"""`regin feature new` records the session that minted the feature and the
project it is filed under — both columns existed; the verb never wrote them.
"""

from __future__ import annotations

import pytest
from sqlmodel import select
from typer.testing import CliRunner

from cli.app import app
from lib.features import events, store
from lib.orm import SessionLocal
from lib.orm.models.features import Feature, FeatureEvent
from lib.orm.models.sync import Repo

runner = CliRunner()


@pytest.fixture()
def r1(tmp_db) -> Repo:
    with SessionLocal() as session:
        row = Repo(name="r1", path="/tmp/r1")
        session.add(row)
        session.commit()
        session.refresh(row)
    return row


def _specced_trace(feature_id: int):
    with SessionLocal() as session:
        return session.exec(
            select(FeatureEvent.trace_id).where(
                FeatureEvent.feature_id == feature_id,
                FeatureEvent.kind == events.SPECCED)).one()


def _only_feature() -> Feature:
    rows = store.population()
    assert len(rows) == 1, rows
    return rows[0]


def test_session_lands_on_the_specced_event(tmp_db):
    result = runner.invoke(app, ["feature", "new", "minted",
                                 "--session", "sess-abc"])
    assert result.exit_code == 0, result.output
    assert _specced_trace(_only_feature().id) == "sess-abc"


def test_project_name_files_the_feature(r1):
    result = runner.invoke(app, ["feature", "new", "filed",
                                 "--project", "r1"])
    assert result.exit_code == 0, result.output
    assert _only_feature().project_id == r1.id


def test_unknown_project_writes_nothing(r1):
    result = runner.invoke(app, ["feature", "new", "nowhere",
                                 "--project", "no-such-repo"])
    assert result.exit_code != 0
    assert "r1" in result.output
    assert store.population() == []


def test_without_flags_both_stay_null(tmp_db):
    result = runner.invoke(app, ["feature", "new", "bare"])
    assert result.exit_code == 0, result.output
    row = _only_feature()
    assert row.project_id is None
    assert _specced_trace(row.id) is None


def test_session_does_not_attribute_the_feature(tmp_db):
    from lib.features import attribution
    runner.invoke(app, ["feature", "new", "minted", "--session", "sess-abc"])
    assert attribution.attributions_for_session("sess-abc") == []


def test_adopted_feature_carries_session_and_project(r1, tracker_issue):
    tracker_issue("CAI-77", title="adopted")
    result = runner.invoke(app, ["feature", "new", "ignored", "--ref",
                                 "CAI-77", "--session", "sess-ref",
                                 "--project", "r1"])
    assert result.exit_code == 0, result.output
    row = store.by_external("linear", "CAI-77")
    assert row.project_id == r1.id
    assert _specced_trace(row.id) == "sess-ref"
