"""All three Pipeline tab counts, from one read, under one scope (regin:73).

A count rendered only on the tab you are already looking at is the one count
that carries no information. These are the rules that make the other two
trustworthy: the scope the shell owns, and the fact that no scope can hide a
row from every reading of the page.
"""

from __future__ import annotations

import pytest
from sqlmodel import text

from lib.auth import create_token
from lib.orm import SessionLocal
from lib.pipeline import counts as counts_module

VIEWER = {"Authorization": f"Bearer {create_token(3, 'vw', 'viewer')}"}


def _file_feature(feature_id: int, project_id):
    with SessionLocal() as session:
        session.exec(text(
            "UPDATE features SET project_id = :p WHERE id = :i"
        ).bindparams(p=project_id, i=feature_id))
        session.commit()


def test_shell_publishes_all_three_counts(flask_client, feature_row,
                                          defect_row, native_task):
    native_task(title="queued")
    feature = feature_row("a feature")
    defect_row(feature.id)
    defect_row(None)

    body = flask_client.get("/api/pipeline/counts", headers=VIEWER).get_json()

    assert set(body["counts"]) == {"tasks", "features", "defects"}
    assert body["counts"]["tasks"] == 1
    assert body["counts"]["features"] == 1
    assert body["counts"]["defects"] == 2


def test_counts_follow_the_project_scope(flask_client, feature_row,
                                         defect_row, native_task):
    filed = feature_row("filed")
    unfiled = feature_row("unfiled")
    _file_feature(filed.id, 7)
    defect_row(filed.id)
    defect_row(unfiled.id)
    native_task(title="filed task", project_id=7)
    native_task(title="unfiled task")

    scoped = flask_client.get("/api/pipeline/counts?project=7",
                              headers=VIEWER).get_json()["counts"]
    assert scoped == {"tasks": 1, "features": 1, "defects": 1}

    none = flask_client.get("/api/pipeline/counts?project=none",
                            headers=VIEWER).get_json()["counts"]
    assert none == {"tasks": 1, "features": 1, "defects": 1}


def test_unattributed_defect_has_no_project(flask_client, feature_row,
                                            defect_row):
    filed = feature_row("filed")
    _file_feature(filed.id, 7)
    defect_row(None)

    counts = counts_module.counts

    assert counts("all")["defects"] == 1
    assert counts(counts_module.SCOPE_NONE)["defects"] == 1
    assert counts(7)["defects"] == 0


def test_no_row_is_invisible_under_every_scope(flask_client, feature_row,
                                               defect_row):
    """`all` = `none` + every project scope, for each of the three."""
    filed = feature_row("filed")
    _file_feature(filed.id, 7)
    feature_row("unfiled")
    defect_row(filed.id)
    defect_row(None)

    every = counts_module.counts("all")
    partitioned = counts_module.counts(counts_module.SCOPE_NONE)
    scoped = counts_module.counts(7)
    for tab in ("tasks", "features", "defects"):
        assert every[tab] == partitioned[tab] + scoped[tab], tab


@pytest.mark.parametrize("raw", ["everything", "1.5", "-", "project:7"])
def test_an_unknown_scope_is_refused_not_widened(flask_client, raw):
    res = flask_client.get(f"/api/pipeline/counts?project={raw}",
                           headers=VIEWER)
    assert res.status_code == 400
    assert "project" in res.get_json()["error"]
