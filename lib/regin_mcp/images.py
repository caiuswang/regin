"""Image tools: attach, remove and fetch the images a feature, task or defect
carries — the same `ImageSet` rows the web composer writes. A comment's
images can be fetched but not attached: a comment is written once, by its
author, with its images in the same post.

A feature's or task's image is cited from its body as `![](image:N)`, so
attaching one can also place that reference; a defect's image is evidence in
its thumbnail strip and is never referenced from the symptom.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

from lib.regin_mcp import _common

Owner = Literal["feature", "task", "defect"]
ReadOwner = Literal["feature", "task", "defect", "comment"]
Place = Literal["append", "none"]

_CITED = ("feature", "task")


def _image_store(owner: str):
    if owner == "feature":
        from lib.features import images
    elif owner == "comment":
        from lib.comments import images
    elif owner == "task":
        from lib.tasks import images
    else:
        from lib.defects import images
    return images


def _require_comment(comment_id: int) -> None:
    from lib.comments import images
    if not images.exists(comment_id):
        raise ValueError(f"no comment {comment_id}")


def _require_owner(owner: str, owner_id: int) -> None:
    {"feature": _common.feature, "task": _common.task,
     "defect": _common.defect, "comment": _require_comment}[owner](owner_id)


def _read_input(path: Optional[str], data_b64: Optional[str]) -> bytes:
    from lib.image_payload import decoded_image
    if (path is None) == (data_b64 is None):
        raise ValueError("path, data_b64: give exactly one of them")
    if data_b64 is not None:
        return decoded_image({"data_b64": data_b64})
    try:
        return Path(path).expanduser().read_bytes()
    except OSError as exc:
        raise ValueError(f"path: cannot read {path!r} ({exc.strerror})") from None


def _body(owner: str, owner_id: int) -> str:
    if owner == "feature":
        return _common.feature(owner_id).body or ""
    return _common.task(owner_id).get("body") or ""


def _write_body(owner: str, owner_id: int, body: str) -> None:
    if owner == "feature":
        from lib.features import store
        store.set_body(owner_id, body)
        return
    from lib.tasks import capture, store
    store.update_task(owner_id, capture.parse_update({"body": body}))


def _place_ref(owner: str, owner_id: int, ref: str) -> bool:
    body = _body(owner, owner_id)
    if ref in body:
        return False
    sep = "" if not body or body.endswith("\n") else "\n"
    _write_body(owner, owner_id, f"{body}{sep}{ref}\n")
    return True


def attach_bytes(owner: str, owner_id: int, data: bytes,
                 idx: Optional[int] = None, place: str = "append") -> dict:
    """Attach `data` and, for a cited owner, place its reference per `place`.

    A reference the body cannot take (it would overflow the body's cap) undoes
    the attach, so a refusal never leaves an orphan image behind.
    """
    from lib.image_payload import wanted_idx
    images = _image_store(owner)
    try:
        row = images.attach(owner_id, data, wanted_idx({"idx": idx}))
    except ValueError as exc:
        raise ValueError(f"image: {exc}") from None
    ref = f"![](image:{row['idx']})" if owner in _CITED else None
    placed = False
    if ref and place == "append":
        try:
            placed = _place_ref(owner, owner_id, ref)
        except ValueError as exc:
            images.remove(owner_id, row["idx"])
            raise _common.refused(exc, "body") from None
    return {"idx": row["idx"], "media_type": row["media_type"],
            "byte_size": row["byte_size"], "ref": ref, "placed": placed,
            "requested_idx": idx}


def read_paths(owner: str, paths) -> list[bytes]:
    """Every file in `paths`, each checked as an image the new `owner` row
    could take — run before that row is created, so a bad path refuses the
    whole call instead of leaving a row behind that a retry would duplicate."""
    from lib.attached_images import check_usable
    out = []
    for n, path in enumerate(paths or ()):
        data = _read_input(path, None)
        try:
            check_usable(data, n, owner)
        except ValueError as exc:
            raise ValueError(f"image_paths: {path}: {exc}") from None
        out.append(data)
    return out


def attach_all(owner: str, owner_id: int, blobs) -> list[dict]:
    return [attach_bytes(owner, owner_id, data) for data in blobs]


def image_attach(owner: Owner, owner_id: int, path: Optional[str] = None,
                 data_b64: Optional[str] = None, idx: Optional[int] = None,
                 place: Place = "append") -> dict:
    """Attach one image (png, jpeg, gif or webp) to a feature, task or defect.

    On a feature or task, `place="append"` appends `![](image:N)` to the
    body unless the body already cites that N; `"none"` leaves the body alone
    (the `ref` is still returned). A defect's image goes to its thumbnail
    strip — `ref` is null and the symptom is never touched.

    Args:
        owner: feature, task or defect.
        owner_id: The owning row's id.
        path: A local image file to read. Give exactly one of path / data_b64.
        data_b64: The image bytes, base64-encoded.
        idx: The N you want — e.g. the N a body already cites. A proposal: if
            it is taken or was used before, the next free N is issued and
            `requested_idx` differs from the returned `idx`.
        place: append (default) or none.
    """
    _require_owner(owner, owner_id)
    data = _read_input(path, data_b64)
    return attach_bytes(owner, owner_id, data, idx, place)


def image_remove(owner: Owner, owner_id: int, idx: int) -> dict:
    """Detach one image. Its N is never reissued, so a reference to it in a
    body reads as detached rather than as a different picture.

    Args:
        owner: feature, task or defect.
        owner_id: The owning row's id.
        idx: The image's N.
    """
    _require_owner(owner, owner_id)
    if not _image_store(owner).remove(owner_id, idx):
        raise ValueError(f"idx: {owner} {owner_id} has no image {idx}")
    return {"removed": True, "idx": idx}


def image_get(owner: ReadOwner, owner_id: int, idx: int):
    """One image: the picture itself, plus the local file it was written to.

    Args:
        owner: feature, task, defect or comment (a `<followup>` line names
            the comment id of an image that was not sent with the launch).
        owner_id: The owning row's id.
        idx: The image's N.
    """
    from mcp.server.fastmcp import Image
    from lib.attached_images import write_file
    _require_owner(owner, owner_id)
    images = _image_store(owner)
    found = images.get_bytes(owner_id, idx)
    if found is None:
        held = ", ".join(str(r["idx"]) for r in images.list_for(owner_id))
        raise ValueError(f"idx: {owner} {owner_id} has no image {idx} "
                         f"(it has: {held or 'none'})")
    data, media_type = found
    path = write_file(owner, owner_id, idx, data, media_type)
    return [Image(data=data, format=media_type.split("/", 1)[-1]),
            {"idx": idx, "media_type": media_type, "byte_size": len(data),
             "path": str(path)}]


TOOLS = (image_attach, image_remove, image_get)
