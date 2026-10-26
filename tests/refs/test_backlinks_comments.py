"""A comment's references count toward its task's backlinks.

The citer is the thread's task — a comment has no page of its own — and a
task cited from both its body and a comment is one item whose `via` names
both.
"""

from __future__ import annotations

from lib.comments import store as comments
from lib.refs.backlinks import referenced_by
from tests.refs.conftest import EDITOR


def _comment(task_id: int, body: str) -> None:
    thread = comments.open_thread("task", str(task_id))
    comments.add_comment(thread["id"], body=body)


def test_comment_reference_appears_in_backlinks(flask_client, task, defect):
    target = defect("the gutter folds")
    cite = f"[D{target.id} gutter](defect:{target.id})"
    by_comment = task("Commented")
    both = task("Both", body=f"fixes {cite}")
    task("Quoted", body="nothing")
    _comment(by_comment["id"], f"this is {cite}")
    _comment(both["id"], f"still {cite}")

    resp = flask_client.get(f"/api/refs/backlinks?ref=defect:{target.id}",
                            headers=EDITOR)
    assert resp.status_code == 200, resp.get_json()
    items = {item["ref"]: item for item in resp.get_json()["items"]}

    assert items[f"task:{by_comment['id']}"]["via"] == ["comment"]
    assert items[f"task:{both['id']}"]["via"] == ["body", "comment"]
    assert [i["ref"] for i in resp.get_json()["items"]].count(
        f"task:{both['id']}") == 1


def test_a_comment_citing_inside_code_is_not_a_backlink(task, defect):
    target = defect("the gutter folds")
    quiet = task("Quiet")
    _comment(quiet["id"], f"`[x](defect:{target.id})`")
    assert referenced_by(f"defect:{target.id}") == []


def test_a_task_citing_itself_from_a_comment_is_left_out(task):
    own = task("Own")
    _comment(own["id"], f"[T{own['id']} me](task:{own['id']})")
    assert referenced_by(f"task:{own['id']}") == []


def test_feature_backlinks_name_body_as_their_via(feature):
    target = feature("Alpha")
    feature("Cites", body=f"[F{target.id} Alpha](feature:{target.id})")
    assert [i["via"] for i in referenced_by(f"feature:{target.id}")] == \
        [["body"]]
