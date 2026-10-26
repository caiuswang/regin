"""Pictures posted with a comment — `comment_images`.

The storage, the ceilings and the `idx` discipline are `lib.attached_images`;
this binds them to the comment table and adds what is specific to a comment:
it is written once, with its images, in one request.

So there is no attach-after-the-fact here. The browser stages pasted bytes,
writes `![](image:N)` at the caret with an `N` it chose, and posts the body
and the images together; `check_post` refuses the whole comment before any
row exists when the body cites an `N` the post did not carry.
"""

from __future__ import annotations

import base64
import re
from typing import Optional

from sqlalchemy import and_, or_, select

from lib.activity_log import get_activity_logger
from lib.attached_images import ImageSet, check_usable
from lib.image_payload import decoded_image
from lib.orm import SessionLocal
from lib.orm.models.comments import Comment, CommentImage
from lib.refs.grammar import code_ranges

log = get_activity_logger("tasks")

_images = ImageSet(CommentImage, Comment, "comment_id", "comment", log)

_IMAGE_REF = re.compile(r"!\[[^\]\n]*\]\(image:([0-9]+)\)")

# Past SQLite's INTEGER range the insert fails after the thread is opened, so
# an out-of-range index has to be refused with the rest of the post.
_IDX_MAX = 2**31 - 1


class ImagesRejected(ValueError):
    """The post's images, or the body's citations of them, are unusable."""


def cited(body: str) -> set[int]:
    """Every `N` an `![](image:N)` outside code in `body` names."""
    code = code_ranges(body or "")
    return {int(m.group(1)) for m in _IMAGE_REF.finditer(body or "")
            if not any(lo < m.end() and m.start() < hi for lo, hi in code)}


def _staged_idx(entry) -> int:
    raw = entry.get("idx") if isinstance(entry, dict) else None
    if not isinstance(raw, int) or isinstance(raw, bool) \
            or not 1 <= raw <= _IDX_MAX:
        raise ImagesRejected(
            f"each image needs an integer idx from 1 to {_IDX_MAX}")
    return raw


def _decoded(entry, already: int) -> tuple[bytes, str]:
    try:
        data = decoded_image(entry)
        return data, check_usable(data, already, "comment")
    except ValueError as exc:
        raise ImagesRejected(f"image {entry.get('idx')}: {exc}") from None


def check_post(body: str, images) -> list[tuple[int, bytes, str]]:
    """`(idx, bytes, media_type)` per posted image, or ImagesRejected.

    Pure: nothing is written, so a refusal leaves no thread, comment or
    image behind. An image the body does not cite is accepted — it is shown
    as an attachment under the text.
    """
    if images is None:
        images = []
    if not isinstance(images, list):
        raise ImagesRejected("images must be a list")
    staged: list[tuple[int, bytes, str]] = []
    for entry in images:
        idx = _staged_idx(entry)
        if any(idx == seen for seen, _, _ in staged):
            raise ImagesRejected(f"image:{idx} is posted twice")
        data, media_type = _decoded(entry, len(staged))
        staged.append((idx, data, media_type))
    missing = sorted(cited(body) - {idx for idx, _, _ in staged})
    if missing:
        names = ", ".join(f"image:{n}" for n in missing)
        raise ImagesRejected(
            f"the body cites {names}, which was not posted with the comment")
    return staged


def stage_in(session, comment_id: int,
             staged: list[tuple[int, bytes, str]]) -> None:
    """Add `check_post`'s images to the transaction writing the comment."""
    for idx, data, media_type in staged:
        _images.stage_in(session, comment_id, idx, data, media_type)


def summaries_for(comment_ids) -> dict[int, list[dict]]:
    return _images.summaries_for(comment_ids)


def views_for(comment_ids) -> dict[int, list[dict]]:
    """`{comment_id: [{idx, media_type, byte_size}, …]}` — what a comment
    carries in the API, the bytes being a separate GET."""
    return {cid: [{k: image[k] for k in ("idx", "media_type", "byte_size")}
                  for image in rows]
            for cid, rows in _images.summaries_for(comment_ids).items()}


def list_for(comment_id: int) -> list[dict]:
    return _images.list_for(comment_id)


def get_bytes(comment_id: int, idx: int) -> Optional[tuple[bytes, str]]:
    return _images.get_bytes(comment_id, idx)


def exists(comment_id: int) -> bool:
    with SessionLocal() as session:
        return session.get(Comment, comment_id) is not None


def payloads(pairs: list[tuple[int, int]]) -> list[dict]:
    """`{media_type, data_b64}` for each `(comment_id, idx)`, in the order
    given — the shape `ImageSet.payload_for` hands a launch."""
    if not pairs:
        return []
    with SessionLocal() as session:
        rows = session.execute(
            select(CommentImage).where(or_(*(
                and_(CommentImage.comment_id == cid, CommentImage.idx == idx)
                for cid, idx in pairs)))).scalars().all()
    by_key = {(row.comment_id, row.idx): row for row in rows}
    return [{"media_type": by_key[key].media_type,
             "data_b64": base64.b64encode(by_key[key].bytes_).decode("ascii")}
            for key in pairs if key in by_key]


def delete_for_comments(session, comment_ids) -> None:
    """Remove these comments' images in the caller's transaction — the
    comment delete paths do not rely on `ON DELETE CASCADE`."""
    ids = list(comment_ids)
    if ids:
        session.execute(CommentImage.__table__.delete().where(
            CommentImage.comment_id.in_(ids)))
