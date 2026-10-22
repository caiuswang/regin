"""image_attach / image_remove / image_get, and image_paths on the create
tools — the composer's `![](image:N)` discipline, driven over MCP."""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ImageContent

from tests.regin_mcp.conftest import EDITOR, GIF, PNG


@pytest.fixture
def feature(call):
    return call("feature_create", title="with a mockup", spec="见图")["id"]


def test_feature_spec_gets_the_image_and_its_reference(call, feature,
                                                       png_path, flask_client):
    out = call("image_attach", owner="feature", owner_id=feature,
               path=png_path)
    assert (out["idx"], out["ref"], out["placed"]) == (1, "![](image:1)", True)
    shown = call("feature_get", feature_id=feature)
    assert shown["spec"].endswith("![](image:1)\n")
    assert [(i["idx"], i["media_type"]) for i in shown["images"]] == [
        (1, "image/png")]
    served = flask_client.get(f"/api/features/{feature}/images/1",
                              headers=EDITOR)
    assert served.status_code == 200
    assert served.data == PNG


def test_a_reference_written_first_is_honoured(call, png_b64):
    body = "步骤\n![](image:1)\n"
    task = call("task_create", title="t", body=body)
    out = call("image_attach", owner="task", owner_id=task["id"],
               data_b64=png_b64, idx=1)
    assert (out["idx"], out["placed"], out["requested_idx"]) == (1, False, 1)
    assert call("task_get", task_id=task["id"])["body"] == body


def test_a_taken_idx_is_reported_not_hidden(call, png_path):
    task = call("task_create", title="t", body="plain")
    call("image_attach", owner="task", owner_id=task["id"], path=png_path,
         place="none")
    out = call("image_attach", owner="task", owner_id=task["id"],
               path=png_path, idx=1, place="none")
    assert (out["idx"], out["requested_idx"], out["placed"]) == (2, 1, False)
    assert out["ref"] == "![](image:2)"
    assert call("task_get", task_id=task["id"])["body"] == "plain"


def test_a_removed_idx_is_never_reissued(call, feature, png_path):
    call("image_attach", owner="feature", owner_id=feature, path=png_path)
    assert call("image_remove", owner="feature", owner_id=feature,
                idx=1) == {"removed": True, "idx": 1}
    again = call("image_attach", owner="feature", owner_id=feature,
                 path=png_path, idx=1, place="none")
    assert (again["idx"], again["requested_idx"]) == (2, 1)
    with pytest.raises(ToolError, match="has no image 1"):
        call("image_remove", owner="feature", owner_id=feature, idx=1)


def test_defect_screenshot_goes_to_the_strip_not_the_symptom(call, png_path):
    opened = call("defect_open", symptom="按钮重叠", image_paths=[png_path])
    shown = call("defect_get", defect_id=opened["id"])
    assert len(shown["images"]) == 1
    assert shown["symptom"] == "按钮重叠"
    out = call("image_attach", owner="defect", owner_id=opened["id"],
               path=png_path)
    assert (out["ref"], out["placed"], out["idx"]) == (None, False, 2)
    assert call("defect_get", defect_id=opened["id"])["symptom"] == "按钮重叠"


def test_create_tools_attach_image_paths(call, png_path):
    feature = call("feature_create", title="f", spec="spec",
                   image_paths=[png_path])
    assert [i["idx"] for i in feature["images"]] == [1]
    assert call("feature_get", feature_id=feature["id"])["spec"] == (
        "spec\n![](image:1)\n")
    task = call("task_create", title="t", image_paths=[png_path, png_path])
    assert [i["idx"] for i in task["images"]] == [1, 2]
    assert task["body"] == "![](image:1)\n![](image:2)"


def test_a_non_image_is_refused_and_nothing_lands(call, feature, tmp_path):
    text = tmp_path / "notes.txt"
    text.write_text("not a picture")
    with pytest.raises(ToolError, match="not a supported image"):
        call("image_attach", owner="feature", owner_id=feature,
             path=str(text))
    shown = call("feature_get", feature_id=feature)
    assert (shown["images"], shown["spec"]) == ([], "见图")


@pytest.mark.parametrize("args,needle", [
    ({}, "exactly one"),
    ({"path": "/x.png", "data_b64": "AA=="}, "exactly one"),
    ({"path": "/nonexistent/shot.png"}, "cannot read '/nonexistent/shot.png'"),
    ({"data_b64": "not base64!"}, "not valid base64"),
])
def test_bad_image_input_names_the_field(call, feature, args, needle):
    with pytest.raises(ToolError, match=needle):
        call("image_attach", owner="feature", owner_id=feature, **args)


def test_unknown_owner_row_is_refused(call, png_path):
    with pytest.raises(ToolError, match="no task #999"):
        call("image_attach", owner="task", owner_id=999, path=png_path)


def test_the_count_ceiling_is_relayed(call, feature, png_path, monkeypatch):
    from lib.settings import settings
    monkeypatch.setattr(settings, "prompt_images_max_count", 1)
    call("image_attach", owner="feature", owner_id=feature, path=png_path)
    with pytest.raises(ToolError, match="at most 1 images per feature"):
        call("image_attach", owner="feature", owner_id=feature, path=png_path)


def test_image_get_returns_the_picture_and_a_file(mcp, call, png_path):
    task = call("task_create", title="t")
    call("image_attach", owner="task", owner_id=task["id"], path=png_path)
    gif = base64.b64encode(GIF).decode("ascii")
    call("image_attach", owner="task", owner_id=task["id"], data_b64=gif)
    out = asyncio.run(mcp.call_tool(
        "image_get", {"owner": "task", "owner_id": task["id"], "idx": 2}))
    blocks = out[0] if isinstance(out, tuple) else out
    image = next(b for b in blocks if isinstance(b, ImageContent))
    assert (image.mimeType, base64.b64decode(image.data)) == ("image/gif", GIF)
    meta = next(b for b in blocks if not isinstance(b, ImageContent))
    path = Path(__import__("json").loads(meta.text)["path"])
    assert path.read_bytes() == GIF
    assert path.name == f"{task['id']}-2.gif"
    with pytest.raises(ToolError, match=r"has no image 7 \(it has: 1, 2\)"):
        asyncio.run(mcp.call_tool(
            "image_get", {"owner": "task", "owner_id": task["id"], "idx": 7}))


@pytest.mark.parametrize("name,args", [
    ("feature_create", {"title": "never lands"}),
    ("task_create", {"title": "never lands"}),
    ("defect_open", {"symptom": "never lands"}),
])
def test_a_bad_image_path_refuses_the_create_and_leaves_no_row(
        call, tmp_path, png_path, name, args):
    text = tmp_path / "notes.txt"
    text.write_text("not a picture")
    for bad, needle in (("/nonexistent/x.png", "cannot read"),
                        (str(text), "not a supported image")):
        with pytest.raises(ToolError, match=needle):
            call(name, image_paths=[png_path, bad], **args)
    assert call("feature_list")["features"] == []
    assert call("task_list", status="")["tasks"] == []
    assert call("defect_list", whole_ledger=True)["defects"] == []


BODY_CAP = 64_000


@pytest.mark.parametrize("name,args,list_call,list_key", [
    ("feature_create",
     {"title": "overflow", "spec": "x" * BODY_CAP},
     ("feature_list",), "features"),
    ("task_create",
     {"title": "overflow", "body": "x" * BODY_CAP},
     ("task_list", {"status": ""}), "tasks"),
])
def test_create_with_image_paths_rolls_back_when_body_is_at_cap(
        call, png_path, name, args, list_call, list_key):
    """A body already at its cap cannot absorb the appended ![](image:N) refs.

    The image attach must roll back and the ToolError must name the body field,
    but the new row must also disappear so a retry does not duplicate it.
    """
    with pytest.raises(ToolError, match="body"):
        call(name, image_paths=[png_path], **args)
    list_args = list_call[1] if len(list_call) > 1 else {}
    assert call(list_call[0], **list_args)[list_key] == []
    # A retry still fails and still leaves no duplicate behind.
    with pytest.raises(ToolError, match="body"):
        call(name, image_paths=[png_path], **args)
    assert call(list_call[0], **list_args)[list_key] == []
