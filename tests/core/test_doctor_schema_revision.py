"""doctor reports the primary DB's alembic revision against the head.

The gap this closes: `regin serve` never touches the schema, so a checkout
that ships a revision the install has not applied runs its new SQL against the
old columns. Every read of the changed table then raises `no such column` and
the surface answers 500 with nothing naming the cause. The rest of the suite
cannot see this — every test DB is built from `db/schema.sql`, which is head
by construction — so the check has to assert on a DB deliberately put behind.
"""

from __future__ import annotations

import sqlite3

from alembic.script import ScriptDirectory

import lib.orm.engine as engine
from lib import db_migrate, doctor


def _head() -> str:
    return ScriptDirectory.from_config(db_migrate._config()).get_current_head()


def _stamp(revision: str) -> None:
    conn = sqlite3.connect(engine.DB_PATH)
    try:
        conn.execute("UPDATE alembic_version SET version_num = ?", (revision,))
        conn.commit()
    finally:
        conn.close()


def _item(items: list[dict]) -> dict:
    return next(i for i in items if i["id"] == "schema_revision")


def test_doctor_passes_when_the_db_is_at_head(tmp_db):
    db_migrate.stamp_head()
    item = _item(doctor._schema_revision_items())
    assert item["present"] is True
    assert _head() in item["version"]


def test_doctor_flags_a_db_behind_head(tmp_db):
    db_migrate.stamp_head()
    _stamp("0001")

    item = _item(doctor._schema_revision_items())

    assert item["present"] is False
    assert "0001" in item["version"] and _head() in item["version"]
    assert "regin migrate" in item["install_hint"]


def test_doctor_flags_an_unstamped_db(tmp_db):
    # A DB with no alembic_version row at all is not silently a pass.
    item = _item(doctor._schema_revision_items())

    assert item["present"] is False
    assert "unstamped" in item["version"]


def test_the_schema_group_is_in_the_report(tmp_db):
    db_migrate.stamp_head()
    names = [g["name"] for g in doctor.run_checks()["groups"]]
    assert "Database schema (alembic)" in names
