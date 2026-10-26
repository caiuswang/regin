"""A comment's own pictures — `comment_images`, written with the comment.

The claims pinned here: the body and its images land in one transaction or
not at all (a refused post leaves no thread, comment or image row), a body
may be nothing but a screenshot, a comment's images are readable over HTTP
and through MCP `image_get`, and `db/schema.sql`, the 0048 revision and the
SQLModel classes describe one shape.
"""

from __future__ import annotations

import base64
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import func, select

from lib import db_migrate
from lib.comments import store as comments
from lib.orm import SessionLocal
from lib.orm.base import metadata
from lib.orm.models.comments import Comment, CommentImage, CommentThread

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
GIF = b"GIF89a" + b"\x00" * 16
_SCHEMA_SQL = Path(__file__).resolve().parents[2] / "db" / "schema.sql"


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _task(client) -> int:
    resp = client.post("/api/tasks", json={"title": "a task"})
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()["task"]["id"]


def _counts() -> tuple[int, int, int]:
    with SessionLocal() as session:
        return tuple(session.execute(select(func.count()).select_from(m))
                     .scalar() for m in (CommentThread, Comment, CommentImage))


def _post(client, task_id, **payload):
    return client.post(f"/api/tasks/{task_id}/comments", json=payload)


def test_comment_citing_missing_image_is_refused(flask_client):
    task_id = _task(flask_client)
    before = _counts()

    resp = _post(flask_client, task_id, body="see ![](image:2)",
                 images=[{"idx": 1, "data_b64": _b64(PNG)}])

    assert resp.status_code == 400
    assert resp.get_json()["field"] == "images"
    assert "image:2" in resp.get_json()["error"]
    assert _counts() == before


def test_an_unusable_image_refuses_the_whole_comment(flask_client):
    task_id = _task(flask_client)
    before = _counts()

    resp = _post(flask_client, task_id, body="look",
                 images=[{"idx": 1, "data_b64": _b64(b"<svg>no</svg>")}])

    assert resp.status_code == 400
    assert resp.get_json()["field"] == "images"
    assert _counts() == before


@pytest.mark.parametrize("idx", [0, True, 2**63, "1"])
def test_an_out_of_range_idx_refuses_without_a_thread(flask_client, idx):
    task_id = _task(flask_client)
    before = _counts()

    resp = _post(flask_client, task_id, body="x",
                 images=[{"idx": idx, "data_b64": _b64(PNG)}])

    assert resp.status_code == 400
    assert resp.get_json()["field"] == "images"
    assert _counts() == before


def test_an_image_cited_only_inside_code_need_not_be_posted(flask_client):
    task_id = _task(flask_client)
    resp = _post(flask_client, task_id, body="write `![](image:3)` to embed")
    assert resp.status_code == 201, resp.get_json()


def test_comment_with_only_an_image_is_accepted(flask_client):
    task_id = _task(flask_client)

    resp = _post(flask_client, task_id, body="",
                 images=[{"idx": 1, "data_b64": _b64(PNG)}])

    assert resp.status_code == 201, resp.get_json()
    comment = resp.get_json()["comment"]
    assert comment["body"] == ""
    assert comment["images"] == [
        {"idx": 1, "media_type": "image/png", "byte_size": len(PNG)}]
    listed = flask_client.get(f"/api/tasks/{task_id}/comments").get_json()
    assert listed["threads"][0]["comments"][0]["images"] == comment["images"]


def test_an_empty_comment_without_images_is_still_refused(flask_client):
    task_id = _task(flask_client)
    resp = _post(flask_client, task_id, body="  ")
    assert resp.status_code == 400
    assert resp.get_json()["field"] == "body"


def test_an_uncited_image_is_kept_as_an_attachment(flask_client):
    task_id = _task(flask_client)
    resp = _post(flask_client, task_id, body="the first ![](image:1)",
                 images=[{"idx": 1, "data_b64": _b64(PNG)},
                         {"idx": 4, "data_b64": _b64(GIF)}])
    assert resp.status_code == 201, resp.get_json()
    assert [i["idx"] for i in resp.get_json()["comment"]["images"]] == [1, 4]
    with SessionLocal() as session:
        cid = resp.get_json()["comment"]["id"]
        assert session.get(Comment, cid).image_high_water == 4


def test_comment_image_is_served_raw(flask_client):
    task_id = _task(flask_client)
    cid = _post(flask_client, task_id, body="![](image:1)",
                images=[{"idx": 1, "data_b64": _b64(PNG)}]
                ).get_json()["comment"]["id"]

    got = flask_client.get(f"/api/comments/{cid}/images/1")
    assert got.status_code == 200
    assert got.data == PNG
    assert got.mimetype == "image/png"
    assert flask_client.get(f"/api/comments/{cid}/images/2").status_code == 404


def test_deleting_a_thread_deletes_its_comment_images(flask_client):
    task_id = _task(flask_client)
    posted = _post(flask_client, task_id, body="x",
                   images=[{"idx": 1, "data_b64": _b64(PNG)}]).get_json()
    comments.delete_thread(posted["thread"]["id"])
    assert _counts() == (0, 0, 0)


def test_image_get_reads_comment_owner(flask_client):
    from lib.regin_mcp.images import image_get

    task_id = _task(flask_client)
    cid = _post(flask_client, task_id, body="![](image:1)",
                images=[{"idx": 1, "data_b64": _b64(PNG)}]
                ).get_json()["comment"]["id"]

    picture, meta = image_get(owner="comment", owner_id=cid, idx=1)

    assert picture.data == PNG
    assert meta["media_type"] == "image/png"
    assert Path(meta["path"]).read_bytes() == PNG
    with pytest.raises(ValueError, match="has no image 2"):
        image_get(owner="comment", owner_id=cid, idx=2)
    with pytest.raises(ValueError, match="no comment"):
        image_get(owner="comment", owner_id=cid + 999, idx=1)


def _shape(path, table: str) -> tuple:
    conn = sqlite3.connect(str(path))
    try:
        cols = [(r[1], r[2].upper(), r[4], r[5]) for r in
                conn.execute(f"PRAGMA table_info({table})").fetchall()]
        notnull = {r[1] for r in
                   conn.execute(f"PRAGMA table_info({table})").fetchall()
                   if r[3] and not r[5]}
        indexes = set()
        for row in conn.execute(f"PRAGMA index_list({table})").fetchall():
            info = conn.execute(f"PRAGMA index_info({row[1]})").fetchall()
            indexes.add((bool(row[2]), row[3], tuple(r[2] for r in info)))
        fks = {(r[2], r[3], r[4], r[6]) for r in
               conn.execute(f"PRAGMA foreign_key_list({table})").fetchall()}
        return cols, notnull, indexes, fks
    finally:
        conn.close()


def test_comment_images_schema_parity(tmp_db, tmp_path):
    fresh = tmp_path / "fresh.db"
    conn = sqlite3.connect(str(fresh))
    try:
        conn.executescript(_SCHEMA_SQL.read_text())
    finally:
        conn.close()

    conn = sqlite3.connect(str(tmp_db))
    try:
        conn.execute("DROP TABLE comment_images")
        conn.execute("ALTER TABLE comments DROP COLUMN image_high_water")
        conn.commit()
    finally:
        conn.close()
    command.stamp(db_migrate._config(), "0046")
    command.upgrade(db_migrate._config(), "head")

    assert _shape(tmp_db, "comment_images") == _shape(fresh, "comment_images")
    assert _shape(tmp_db, "comments") == _shape(fresh, "comments")
    _assert_models_match(fresh)


def _assert_models_match(fresh) -> None:
    model = metadata.tables["comment_images"]
    names = [c[0] for c in _shape(fresh, "comment_images")[0]]
    assert sorted(c.name for c in model.columns) == sorted(names)
    assert "image_high_water" in metadata.tables["comments"].columns
    assert [tuple(c.name for c in ix.columns) for ix in model.indexes] == \
        [("comment_id",)]
