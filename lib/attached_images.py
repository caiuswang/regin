"""Images hung off a row that outlives them — tasks, features, and defects.

The composer's problem here is the mirror of the bridge's. `BridgeAttachment`
holds bytes that have no prompt *yet* and are spent by the first one that
claims them; a task's or a feature's images have a row to hang off from the
moment it exists, and they are re-read by every attempt. So nothing is claimed,
nothing is swept, and `idx` is a plain per-owner order rather than an
`[Image #N]` marker some text has to carry: the prompt is a title and a body,
and the images ride the launch as content blocks beside it.

Validation is `attachments.sniff_media_type` — magic bytes, not the
`Content-Type` a paste event claimed — and the ceilings are the same
`prompt_image_max_bytes` / `prompt_images_max_count` settings the bridge
composer answers to, because these bytes end up in the same place: a model's
context.

One `ImageSet` per owning table. The three owners are separate tables rather
than one with a nullable owner column, because they differ in what the bytes
are FOR: a task's image is delivered with that task alone, a feature's with
every slice cut from it, and a defect's with nothing at all — it is evidence
the reporter saw, read by whoever opens the row. Folding them together would
put three owners on one column and make "whose image is this" a join away from
ambiguous.
"""

from __future__ import annotations

import base64
import hashlib
from typing import Optional

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from lib.agent_bridge.attachments import sniff_media_type
from lib.orm import SessionLocal

# Attempts to win a free `idx` before giving up. Concurrency here is two
# browser tabs on one row, not a fleet.
_ALLOC_ATTEMPTS = 5


def summarize(row) -> dict:
    """The metadata a page needs. Never the bytes — those are a separate GET,
    so a 50-row list does not carry a megabyte of base64."""
    return {"id": row.id, "idx": row.idx, "media_type": row.media_type,
            "byte_size": row.byte_size}


class ImageSet:
    """The image table of one owning entity, named by `noun`."""

    def __init__(self, model, owner_model, owner_field: str, noun: str, log):
        self.model = model
        self.owner_model = owner_model
        self.owner_field = owner_field
        self.noun = noun
        self.log = log

    @property
    def _owner(self):
        return getattr(self.model, self.owner_field)

    def _limits(self) -> tuple[int, int]:
        from lib.settings import settings  # noqa: PLC0415

        return settings.prompt_image_max_bytes, settings.prompt_images_max_count

    def _reject_unusable(self, owner_id: int, data: bytes) -> str:
        """The media type, or ValueError with a sentence the operator can
        read."""
        media_type = sniff_media_type(data)
        if media_type is None:
            raise ValueError("not a supported image (png, jpeg, gif or webp)")
        max_bytes, max_count = self._limits()
        if len(data) > max_bytes:
            raise ValueError(f"image is larger than {max_bytes} bytes")
        if self._count(owner_id) >= max_count:
            raise ValueError(
                f"at most {max_count} images per {self.noun}")
        return media_type

    def _count(self, owner_id: int) -> int:
        with SessionLocal() as session:
            return len(session.execute(
                select(self.model.id)
                .where(self._owner == owner_id)).scalars().all())

    def attach(self, owner_id: int, data: bytes,
               want_idx: Optional[int] = None) -> dict:
        """Record `data` against `owner_id`. Raises ValueError with the
        reason.

        `want_idx` is a *proposal*, and exists for the composer case: bytes
        pasted before the row existed already have an `![](image:N)` reference
        written into the body at the caret, so the upload that finally lands
        them has to be able to ask for the N the text already names. A taken
        index falls back to the next free one rather than refusing — the
        allocator stays total, and a reference left pointing at nothing renders
        as visible text rather than a broken picture.
        """
        media_type = self._reject_unusable(owner_id, data)
        row = self._insert_with_next_idx(owner_id, media_type, data, want_idx)
        self.log.write(f"{self.noun}_image_attached",
                       **{self.owner_field: owner_id}, idx=row["idx"],
                       byte_size=row["byte_size"], media_type=media_type)
        return row

    def _high_water(self, session, owner_id: int) -> int:
        """The highest `idx` ever issued to this owner, freed or not."""
        marked = session.execute(
            select(self.owner_model.image_high_water)
            .where(self.owner_model.id == owner_id)).scalar() or 0
        used = session.execute(
            select(func.max(self.model.idx)).where(self._owner == owner_id)
        ).scalar() or 0
        return max(marked, used)

    def _next_idx(self, session, owner_id: int) -> int:
        return self._high_water(session, owner_id) + 1

    def _free_idx(self, session, owner_id: int,
                  want_idx: Optional[int]) -> int:
        if want_idx is None or want_idx < 1:
            return self._next_idx(session, owner_id)
        taken = session.execute(
            select(self.model.id).where(self._owner == owner_id,
                                        self.model.idx == want_idx)
        ).scalars().first()
        spent = want_idx <= self._high_water(session, owner_id) and not taken
        return (self._next_idx(session, owner_id)
                if taken or spent else want_idx)

    def _insert_with_next_idx(self, owner_id: int, media_type: str,
                              data: bytes,
                              want_idx: Optional[int] = None) -> dict:
        """Insert, taking `want_idx` when it is free and the next free `idx`
        otherwise.

        Reading the high-water mark and inserting are two statements and the
        edit sheet can be open in two tabs, so `UNIQUE(owner, idx)` is the real
        allocator — this just loses the race gracefully instead of turning a
        second paste into a 500. A proposal that loses that race is dropped on
        the retry, not re-proposed, or a permanently taken index would spin.
        """
        digest = hashlib.sha256(data).hexdigest()
        for _ in range(_ALLOC_ATTEMPTS):
            with SessionLocal() as session:
                idx = self._free_idx(session, owner_id, want_idx)
                want_idx = None
                row = self.model(
                    **{self.owner_field: owner_id}, idx=idx,
                    media_type=media_type, bytes_=data, byte_size=len(data),
                    sha256=digest)
                session.add(row)
                session.execute(
                    update(self.owner_model)
                    .where(self.owner_model.id == owner_id)
                    .values(image_high_water=func.max(
                        self.owner_model.image_high_water, idx)))
                try:
                    session.commit()
                except IntegrityError:
                    session.rollback()
                    continue
                session.refresh(row)
                return summarize(row)
        raise ValueError("too many images being attached at once — try again")

    def list_for(self, owner_id: int) -> list[dict]:
        """This owner's images in delivery order."""
        return self.summaries_for([owner_id]).get(owner_id, [])

    def summaries_for(self, owner_ids) -> dict[int, list[dict]]:
        """`{owner_id: [summary, …]}` for a whole page in one query.

        A per-row read would be an N+1 against a BLOB table — the bytes column
        is excluded here for the same reason.
        """
        ids = list(owner_ids)
        if not ids:
            return {}
        with SessionLocal() as session:
            rows = session.execute(
                select(self.model.id, self._owner, self.model.idx,
                       self.model.media_type, self.model.byte_size)
                .where(self._owner.in_(ids))
                .order_by(self._owner, self.model.idx)
            ).all()
        out: dict[int, list[dict]] = {}
        for row in rows:
            out.setdefault(row[1], []).append(
                {"id": row[0], "idx": row[2], "media_type": row[3],
                 "byte_size": row[4]})
        return out

    def get_bytes(self, owner_id: int, idx: int) -> Optional[tuple[bytes, str]]:
        """`(bytes, media_type)` for one image, or None."""
        with SessionLocal() as session:
            row = session.execute(
                select(self.model).where(self._owner == owner_id,
                                         self.model.idx == idx)
            ).scalars().first()
        return (row.bytes_, row.media_type) if row else None

    def remove(self, owner_id: int, idx: int) -> bool:
        """Drop one image. False when there was nothing at that index.

        A freed `idx` is never reissued, whether it sat in the middle or at
        the top: the owner row's `image_high_water` outlives the image, so an
        `![](image:N)` already sitting in some prose keeps reading as detached
        instead of resolving to a different picture.
        """
        with SessionLocal() as session:
            result = session.execute(
                delete(self.model).where(self._owner == owner_id,
                                         self.model.idx == idx))
            session.commit()
        removed = bool(result.rowcount)
        if removed:
            self.log.write(f"{self.noun}_image_removed",
                           **{self.owner_field: owner_id}, idx=idx)
        return removed

    def payload_for(self, owner_id: int) -> list[dict]:
        """These images as `runner._query_payload` wants them.

        `{media_type, data_b64}` per entry, in `idx` order — the same shape the
        bridge composer's staged images are handed to a turn in, so one prompt
        path carries both.
        """
        with SessionLocal() as session:
            rows = session.execute(
                select(self.model)
                .where(self._owner == owner_id)
                .order_by(self.model.idx)
            ).scalars().all()
        return [{"media_type": row.media_type,
                 "data_b64": base64.b64encode(row.bytes_).decode("ascii")}
                for row in rows]
