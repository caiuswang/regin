"""Transcription is mechanical: idempotent on the slice id, and refusing
rather than guessing at anything the table did not say."""

from __future__ import annotations

import pytest

from lib.orm import SessionLocal
from lib.orm.models.tasks import (
    AgentTask, TASK_STATUS_DONE, TASK_STATUS_RUNNING,
)
from lib.tasks import deps as deps_module
from lib.tasks.transcribe import TranscribeRefused, transcribe

HEADER = ("| # | Slice | Delivers | Spec refs | Tests | Depends on | Base | "
          "Schema |\n|---|---|---|---|---|---|---|---|\n")


def table(*rows: str) -> str:
    return "## Work slices\n\n" + HEADER + "".join(row + "\n" for row in rows)


FOUR = table(
    "| S0 | contracts | the two contracts | R1 | test_a | — | `master` | |",
    "| S1 | runner | the verify runner | R4 | test_b | S0 | f76/integration "
    "| |",
    "| S2 | notice | the broken-chain notice | R5 | test_c | S0 | "
    "f76/integration | |",
    "| S3 | tail | the tail template | R7 | test_d | S1, S2 | "
    "f76/integration | |",
)


def _run(feature_id: int, body: str):
    return transcribe(feature_id, body, branch_prefix="f76/")


def _slices(feature_id: int) -> list[AgentTask]:
    from sqlmodel import select

    with SessionLocal() as session:
        return list(session.exec(
            select(AgentTask).where(AgentTask.feature_id == feature_id)
            .order_by(AgentTask.id)).all())


def _by_slice(feature_id: int, n: str) -> AgentTask:
    return next(t for t in _slices(feature_id) if t.title.startswith(n + " "))


def test_transcribe_is_idempotent_on_slice_id(tmp_db, feature_row):
    feature = feature_row("f76")
    _run(feature.id, FOUR)
    assert len(_slices(feature.id)) == 4

    renamed = FOUR.replace("| S2 | notice |", "| S2 | the notice |")
    again = _run(feature.id, renamed)

    assert len(_slices(feature.id)) == 4
    assert [o.action for o in again.outcomes] == ["updated"] * 4
    assert _by_slice(feature.id, "S2").title == "S2 — the notice"


def test_transcribe_refuses_a_running_slice(tmp_db, feature_row):
    feature = feature_row("f76")
    first = _run(feature.id, FOUR)
    s2 = _by_slice(feature.id, "S2")
    with SessionLocal() as session:
        row = session.get(AgentTask, s2.id)
        row.status = TASK_STATUS_RUNNING
        session.add(row)
        session.commit()

    renamed = FOUR.replace("| S2 | notice |", "| S2 | the notice |")
    again = _run(feature.id, renamed)

    outcomes = {o.n: o for o in again.outcomes}
    assert outcomes["S2"].action == "refused"
    assert [o.n for o in again.refused] == ["S2"]
    assert TASK_STATUS_RUNNING in outcomes["S2"].detail
    assert _by_slice(feature.id, "S2").title == "S2 — notice"
    assert all(outcomes[n].action == "updated" for n in ("S0", "S1", "S3"))
    assert [o.task_id for o in first.outcomes] == \
        [outcomes[o.n].task_id for o in first.outcomes]


def test_a_settled_slice_is_left_alone_and_said_so(tmp_db, feature_row):
    feature = feature_row("f76")
    _run(feature.id, FOUR)
    s1 = _by_slice(feature.id, "S1")
    with SessionLocal() as session:
        row = session.get(AgentTask, s1.id)
        row.status = TASK_STATUS_DONE
        session.add(row)
        session.commit()

    again = _run(feature.id, FOUR.replace("| S1 | runner |", "| S1 | rerun |"))

    outcome = {o.n: o for o in again.outcomes}["S1"]
    assert outcome.action == "untouched"
    assert "not updated" in outcome.detail
    assert _by_slice(feature.id, "S1").title == "S1 — runner"


def test_transcribe_refuses_an_unknown_dependency(tmp_db, feature_row):
    feature = feature_row("f76")
    body = table(
        "| S0 | contracts | the contracts | R1 | test_a | S9 | `master` | |")

    with pytest.raises(TranscribeRefused) as caught:
        _run(feature.id, body)

    assert "S9" in str(caught.value)
    assert _slices(feature.id) == []


def test_transcribe_refuses_a_cycle(tmp_db, feature_row):
    feature = feature_row("f76")
    body = table(
        "| S0 | a | delivers a | R1 | test_a | S1 | `master` | |",
        "| S1 | b | delivers b | R2 | test_b | S0 | `master` | |",
    )

    with pytest.raises(TranscribeRefused) as caught:
        _run(feature.id, body)

    assert "cycle" in str(caught.value)
    assert _slices(feature.id) == []


def test_transcribe_refuses_a_row_missing_base(tmp_db, feature_row):
    feature = feature_row("f76")
    body = table(
        "| S0 | contracts | the contracts | R1 | test_a | — | `master` | |",
        "| S1 | runner | the runner | R4 | test_b | S0 | — | |",
    )

    with pytest.raises(TranscribeRefused) as caught:
        _run(feature.id, body)

    assert caught.value.row == "S1"
    assert caught.value.column == "base"
    assert _slices(feature.id) == []


def test_transcribe_refuses_two_slices_changing_schema(tmp_db, feature_row):
    feature = feature_row("f76")
    body = table(
        "| S0 | a | an alembic revision | R1 | test_a | — | `master` | yes |",
        "| S1 | b | another one | R2 | test_b | — | `master` | yes |",
    )

    with pytest.raises(TranscribeRefused) as caught:
        _run(feature.id, body)

    assert "S0" in str(caught.value) and "S1" in str(caught.value)
    assert _slices(feature.id) == []


def test_a_slice_delivering_the_command_table_claims_no_schema_change(
        tmp_db, feature_row):
    """The claim is a column, not a word sniffed out of prose: `migrate` is
    one of the three keys the command table must carry."""
    feature = feature_row("f76")
    body = table(
        "| S0 | contracts | test / migrate / e2e, and the schema of the "
        "slice table | R1 | test_a | — | `master` | |",
        "| S1 | the migration | one alembic revision | R2 | test_b | — | "
        "`master` | yes |",
    )

    result = _run(feature.id, body)

    assert [o.action for o in result.outcomes] == ["created", "created"]


def test_a_renamed_slice_task_is_still_the_same_slice(tmp_db, feature_row):
    """Identity is `(source, external_id)`, not the title — a human editing
    a slice's title must not make transcription write a second row."""
    feature = feature_row("f76")
    first = _run(feature.id, FOUR)
    s2 = {o.n: o.task_id for o in first.outcomes}["S2"]
    with SessionLocal() as session:
        row = session.get(AgentTask, s2)
        row.title = "notice (renamed by a human)"
        session.add(row)
        session.commit()

    again = _run(feature.id, FOUR)

    assert len(_slices(feature.id)) == 4
    outcome = {o.n: o for o in again.outcomes}["S2"]
    assert outcome.action == "updated"
    assert outcome.task_id == s2
    assert _by_slice(feature.id, "S2").title == "S2 — notice"


def test_a_refused_slice_names_the_edges_it_left_behind(tmp_db, feature_row):
    feature = feature_row("f76")
    first = _run(feature.id, FOUR)
    ids = {o.n: o.task_id for o in first.outcomes}
    with SessionLocal() as session:
        row = session.get(AgentTask, ids["S3"])
        row.status = TASK_STATUS_RUNNING
        session.add(row)
        session.commit()

    dropped = FOUR.replace("| test_d | S1, S2 |", "| test_d | S1 |")
    again = _run(feature.id, dropped)

    outcome = {o.n: o for o in again.outcomes}["S3"]
    assert outcome.action == "refused"
    assert outcome.stale_deps == [ids["S2"]]
    assert "no longer declares" in outcome.detail
    assert sorted(deps_module.depends_on(ids["S3"])) == \
        sorted([ids["S1"], ids["S2"]])


def test_transcribe_writes_worktree_base_from_the_table(tmp_db, feature_row):
    feature = feature_row("f76")
    result = _run(feature.id, FOUR)

    arms = {o.n: o.arm for o in result.outcomes}
    assert arms["S1"]["worktree"]["base"] == "f76/integration"
    assert arms["S1"]["worktree"]["branch"] == "f76/s1"
    assert arms["S0"]["worktree"]["base"] == "master"


def test_transcribe_writes_the_edges_the_table_declares(tmp_db, feature_row):
    feature = feature_row("f76")
    result = _run(feature.id, FOUR)
    ids = {o.n: o.task_id for o in result.outcomes}

    assert deps_module.depends_on(ids["S0"]) == []
    assert deps_module.depends_on(ids["S1"]) == [ids["S0"]]
    assert sorted(deps_module.depends_on(ids["S3"])) == \
        sorted([ids["S1"], ids["S2"]])


def test_a_slice_body_carries_the_four_sections(tmp_db, feature_row):
    feature = feature_row("f76")
    _run(feature.id, FOUR)

    body = _by_slice(feature.id, "S1").body
    assert "## Delivers" in body
    assert "## Spec 依据" in body
    assert "## 验收测试名" in body
    assert "## 收尾动作" in body
    assert "the verify runner" in body
    assert "`test_b`" in body
    assert "f76/integration" in body


def test_every_transcribed_slice_is_an_idea(tmp_db, feature_row):
    feature = feature_row("f76")
    _run(feature.id, FOUR)

    assert {t.status for t in _slices(feature.id)} == {"idea"}


def test_a_slice_moved_to_another_feature_is_reclaimed(tmp_db, feature_row):
    feature, other = feature_row("f76"), feature_row("f77")
    first = _run(feature.id, FOUR)
    s0 = {o.n: o.task_id for o in first.outcomes}["S0"]
    from lib.tasks import store

    store.update_task(s0, {"feature_id": other.id})

    _run(feature.id, FOUR)

    with SessionLocal() as session:
        assert session.get(AgentTask, s0).feature_id == feature.id


def test_a_denial_in_the_schema_cell_is_not_a_claim(tmp_db, feature_row):
    feature = feature_row("f76")
    body = table(
        "| S0 | a | delivers a | R1 | test_a | — | `master` | no schema "
        "change |",
        "| S1 | b | the one migration | R2 | test_b | — | `master` | yes |",
    )

    result = _run(feature.id, body)

    assert [o.action for o in result.outcomes] == ["created", "created"]
