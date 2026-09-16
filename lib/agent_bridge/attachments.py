"""Agent bridge — images staged by a web composer on their way to a session.

A composer paste has the bytes before it has a prompt: the operator drops a
screenshot in, then types the sentence that goes with it, and only the send
produces a turn. `prompt_images` cannot hold that — it is keyed to a settled
`prompt_span_id` — so the bytes wait here, numbered.

The number is the whole design. Each staged image is handed an `[Image #N]`
index allocated per trace, which is the same marker Claude Code writes when an
image is pasted into the raw terminal, and it travels inside the delivered
text. When the prompt span eventually lands, that marker names the row: the
join is by identifier, never by "which upload happened near this turn". A row
is copied into `prompt_images` and stamped `claimed_span_id` exactly once, so
a replayed or re-ingested prompt cannot duplicate the bytes.

The two delivery tiers need different things from a row and the schema carries
both: the SDK tier reads `bytes` and sends them inline as a base64 content
block, while the tmux tier cannot carry bytes at all and is handed
`file_path` — the image materialized on disk — to reference by path. Those
files live in regin's own machine-global data dir rather than in the session's
project, and they are disposable: the durable copy is the `prompt_images` blob,
so `discard_files` can reclaim them once the session is over.

Validation is by magic bytes, not by the `Content-Type` the browser claimed:
the caller is a paste event, and the only thing that makes these bytes safe to
hand back out of the image endpoint is that we checked them ourselves.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import uuid
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from lib.activity_log import get_activity_logger
from lib.orm.models import BridgeAttachment, PromptImage, Session
from lib.orm.engine import SessionLocal
from lib.settings import settings
from lib.trace.prompt_images_resolve import parse_markers

log = get_activity_logger("agent_bridge")

# Attempts to win a free `[Image #N]` slot before giving up (see
# `_insert_with_next_idx`). Concurrency here is two browser tabs, not a
# fleet, so a handful of retries covers it without spinning.
_ALLOC_ATTEMPTS = 5

# Traces reclaimed per paste by the backstop sweep. Small on purpose: the
# operator is waiting on this request, and SessionEnd does the real work.
_SWEEP_BATCH = 20

# The marker Claude Code writes for a pasted image, and the anchor a delivered
# file reference is placed at.
_MARKER_RE = re.compile(r"\[Image #(\d+)\]")

# Leading bytes → media type. WebP needs the `WEBP` at offset 8 as well as the
# `RIFF` at 0; a bare RIFF container is an audio file just as often.
_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


def sniff_media_type(data: bytes) -> str | None:
    """The image media type `data` really is, or None if it is not an image."""
    for magic, media_type in _SIGNATURES:
        if data.startswith(magic):
            return media_type
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _next_idx(session, trace_id: str) -> int:
    """The `[Image #N]` number to hand the next paste on this trace.

    Counts every row the trace ever staged, claimed ones included, so a number
    is never reused: `idx` is what a marker in some already-delivered prompt
    text points at, and re-issuing it would re-aim that marker at new bytes.
    """
    used = session.execute(
        select(BridgeAttachment.idx).where(
            BridgeAttachment.trace_id == trace_id)
    ).scalars().all()
    return (max(used) + 1) if used else 1


def staged_count(trace_id: str) -> int:
    """Images staged and not yet claimed by a prompt span on this trace."""
    with SessionLocal() as session:
        rows = session.execute(
            select(BridgeAttachment.id).where(
                BridgeAttachment.trace_id == trace_id,
                BridgeAttachment.claimed_span_id.is_(None))
        ).scalars().all()
    return len(rows)


def stage(trace_id: str, data: bytes) -> dict:
    """Record `data` as a pending attachment on `trace_id`.

    Returns `{id, idx, media_type, byte_size, marker}`. Raises ValueError with
    an operator-readable reason when the bytes are not a supported image, are
    over `prompt_image_max_bytes`, or would exceed `prompt_images_max_count`
    still-unclaimed images on this trace.
    """
    if not trace_id:
        raise ValueError("no session")
    media_type = sniff_media_type(data)
    if media_type is None:
        raise ValueError("not a supported image (png, jpeg, gif or webp)")
    if len(data) > settings.prompt_image_max_bytes:
        raise ValueError(
            f"image is larger than {settings.prompt_image_max_bytes} bytes")
    if staged_count(trace_id) >= settings.prompt_images_max_count:
        raise ValueError(
            f"at most {settings.prompt_images_max_count} images per prompt")

    _sweep_ended_sessions()
    row_id = uuid.uuid4().hex
    idx = _insert_with_next_idx(row_id, trace_id, media_type, data)
    log.write("bridge_attachment_staged", trace_id=trace_id, idx=idx,
              byte_size=len(data), media_type=media_type)
    return {"id": row_id, "idx": idx, "media_type": media_type,
            "byte_size": len(data), "marker": f"[Image #{idx}]"}


def _insert_with_next_idx(row_id: str, trace_id: str, media_type: str,
                          data: bytes) -> int:
    """Insert the row, taking the next free `[Image #N]` for this trace.

    Reading the high-water mark and inserting are two statements, and both
    composers can be open on the same trace at once, so the unique index on
    `(trace_id, idx)` is the real allocator — this just loses the race
    gracefully instead of turning a second paste into a 500. Bounded: a
    caller that cannot win a slot in this many tries is told so, rather than
    spinning.
    """
    digest = hashlib.sha256(data).hexdigest()
    for _ in range(_ALLOC_ATTEMPTS):
        with SessionLocal() as session:
            idx = _next_idx(session, trace_id)
            session.add(BridgeAttachment(
                id=row_id, trace_id=trace_id, idx=idx, media_type=media_type,
                bytes_=data, byte_size=len(data), sha256=digest,
            ))
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                continue
            return idx
    raise ValueError("too many images being attached at once — try again")


def by_indices(trace_id: str, indices: list[int], *,
               unclaimed_only: bool = True) -> list[BridgeAttachment]:
    """Staged rows for `indices`, in the order the indices are given.

    Delivery wants `unclaimed_only`: an image goes out with one prompt, and a
    marker pointing at a spent row is a prompt being re-read, not a second
    send. Binding does not, because one prompt produces MORE THAN ONE span —
    the live placeholder and the transcript's own row coexist by design
    (`ingest_session_spans` is append-only; `lib/trace/merge.py` picks a
    winner at read time). Whichever the reader ends up seeing has to carry the
    image, so both are bound and `prompt_images`' primary key does the
    de-duplication.
    """
    if not indices:
        return []
    filters = [BridgeAttachment.trace_id == trace_id,
               BridgeAttachment.idx.in_(indices)]
    if unclaimed_only:
        filters.append(BridgeAttachment.claimed_span_id.is_(None))
    with SessionLocal() as session:
        rows = session.execute(
            select(BridgeAttachment).where(*filters)).scalars().all()
    by_idx = {row.idx: row for row in rows}
    return [by_idx[i] for i in indices if i in by_idx]


def text_digest(text: str) -> str:
    """A record of what a delivery went out as — for audit, not for matching.

    It cannot be an identity: the harness re-renders the text it was handed
    before the transcript writes it back, so the prompt span's text and the
    dispatched text differ for one and the same prompt. A slash command
    returns as `<command-name> + ' ' + <command-args>` with each half
    stripped, which collapses any interior run of spaces the composer sent.
    """
    return hashlib.sha256((text or "").strip().encode("utf-8")).hexdigest()


def _mark_dispatched(rows: list[BridgeAttachment], text: str) -> None:
    """Mark each row as sent, and record the text it went out as.

    Being dispatched is what makes a row bindable at all, and that is the
    discriminator a marker needs. `[Image #1]` in a prompt span does NOT mean
    "the composer's first image": Claude Code numbers its own terminal pastes
    from 1 within the same trace, out of a cache regin never writes. Both
    counters produce `[Image #1]` for different pictures, so a row still
    sitting unsent in the composer must never answer to one — otherwise a
    terminal-typed prompt swallows a draft's image.

    The text is kept for audit only; see `text_digest` for why matching a span
    back to it does not work.
    """
    if not rows:
        return
    digest = text_digest(text)
    with SessionLocal() as session:
        session.execute(
            update(BridgeAttachment)
            .where(BridgeAttachment.id.in_([row.id for row in rows]))
            .values(dispatched_sha=digest))
        session.commit()


def mark_claimed(trace_id: str, idx: int, span_id: str) -> bool:
    """Stamp the staged row for `idx` as belonging to `span_id`.

    Conditional on the row still being unclaimed, so two ingests racing the
    same prompt cannot both copy the bytes. Returns whether this call is the
    one that won.
    """
    with SessionLocal() as session:
        result = session.execute(
            update(BridgeAttachment)
            .where(BridgeAttachment.trace_id == trace_id,
                   BridgeAttachment.idx == idx,
                   BridgeAttachment.claimed_span_id.is_(None))
            .values(claimed_span_id=span_id))
        session.commit()
    return bool(result.rowcount)


def settled_indices(trace_id: str, span_id: str) -> list[int]:
    """Every `[Image #N]` this span already has bytes for.

    The bind is a no-op the second time a span is ingested — the transcript
    re-posts a span it has seen, and `prompt_images`' primary key rejects the
    copy. Deriving the span's `image_indices` from what the bind happened to
    return therefore blanks the attribute on that replay, because the ingest
    upserts attributes wholesale. Reading the settled rows instead makes the
    stamp idempotent: it says what is true, not what this call did.
    """
    with SessionLocal() as session:
        return sorted(session.execute(
            select(PromptImage.idx).where(
                PromptImage.trace_id == trace_id,
                PromptImage.prompt_span_id == span_id)).scalars().all())


def _claim_and_copy(row: BridgeAttachment, trace_id: str,
                    span_id: str) -> bool:
    """Settle one row onto `span_id`, claim and copy in a single transaction.

    Both halves commit together or neither does. Split across two commits, a
    crash — or an `IntegrityError` from a `prompt_images` row that already
    exists — leaves the staged row claimed but never copied, which is an image
    nothing will ever render and nothing will ever retry.
    """
    with SessionLocal() as session:
        # Best-effort: records the FIRST span that took this image, for audit.
        # It is not the guard — a second span for the same prompt must still
        # be bound, and `prompt_images`' primary key is what stops a repeat.
        session.execute(
            update(BridgeAttachment)
            .where(BridgeAttachment.id == row.id,
                   BridgeAttachment.claimed_span_id.is_(None))
            .values(claimed_span_id=span_id))
        session.add(PromptImage(
            trace_id=trace_id, prompt_span_id=span_id, idx=row.idx,
            media_type=row.media_type, bytes_=row.bytes_,
            byte_size=row.byte_size, sha256=row.sha256,
        ))
        try:
            session.commit()
        except IntegrityError:
            # This span already carries an image at this index — a replay, or
            # the transcript hook got there first. Nothing to add.
            session.rollback()
            return False
    return True


_EXTENSIONS = {"image/png": ".png", "image/jpeg": ".jpg",
               "image/gif": ".gif", "image/webp": ".webp"}


def materialize(row: BridgeAttachment, directory: Path) -> str:
    """Write `row`'s bytes under `directory` and record where they went.

    The tmux tier's reason for existing: keystrokes cannot carry an image, so
    the pane is given a path instead. The name is derived from the row id
    rather than from anything the browser sent — a client-supplied filename is
    a path-traversal argument, and nothing downstream needs it.
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{row.id}{_EXTENSIONS.get(row.media_type, '.png')}"
    path.write_bytes(row.bytes_)
    os.chmod(path, 0o600)
    with SessionLocal() as session:
        session.execute(
            update(BridgeAttachment)
            .where(BridgeAttachment.id == row.id)
            .values(file_path=str(path)))
        session.commit()
    log.write("bridge_attachment_materialized", attachment_id=row.id,
              path=str(path))
    return str(path)


def _delivery_dir(trace_id: str) -> Path:
    """Where the tmux tier materializes an image for `trace_id`.

    Machine-global, outside every project. Writing into the session's own cwd
    gave a shorter relative reference, but it also dropped untracked binaries
    into whatever repo the session happened to be running in: regin's own tree
    ignores `.regin/`, and no other project does, so the mess was invisible
    here and visible everywhere else. Silently littering someone's working
    tree is the worse failure, and it is the one they cannot opt out of.

    The cost is an absolute path in the delivered prompt, and — outside
    `bypassPermissions` — a `Read` approval for a file beyond the project
    root. Both are visible and recoverable; a stray PNG in `git status` is
    neither.
    """
    return Path(settings.data_dir) / "pasted" / trace_id


def for_sdk(trace_id: str, text: str) -> list[dict]:
    """`{media_type, data_b64}` for every `[Image #N]` marker in `text`.

    The SDK tier carries the bytes themselves, so nothing is written to disk.
    """
    rows = by_indices(trace_id, parse_markers(text))
    _mark_dispatched(rows, text)
    return [{"media_type": row.media_type,
             "data_b64": base64.b64encode(row.bytes_).decode("ascii")}
            for row in rows]


def for_tmux(trace_id: str, text: str) -> str:
    """`text` with an `@path` prepended for each image it marks.

    Keystrokes cannot carry bytes, so this tier gets files and references them
    by path. The `[Image #N]` markers stay in the text alongside those paths:
    the path is what the agent opens, while the marker is what binds the stored
    bytes to this prompt's span so the trace UI can draw the thumbnail.
    """
    rows = by_indices(trace_id, parse_markers(text))
    if not rows:
        return text
    directory = _delivery_dir(trace_id)
    refs = {row.idx: materialize(row, directory) for row in rows}

    # In the terminal an image lands where the cursor is, and the operator
    # composed this sentence around that spot. Hoisting the paths to the front
    # would move the picture away from the words that point at it — "look at
    # this, then the error below" stops meaning anything. So each reference is
    # substituted AT its own marker, and the marker stays put beside it: `@` is
    # what the agent opens, the marker is where the reader draws the thumbnail.
    def _place(match):
        ref = refs.get(int(match.group(1)))
        return f"@{ref} {match.group(0)}" if ref else match.group(0)

    delivered = _MARKER_RE.sub(_place, text)
    _mark_dispatched(rows, delivered)
    return delivered


def _sweep_ended_sessions() -> None:
    """Reclaim files left by sessions that never reached `SessionEnd`.

    The hook is the primary mechanism; this is the backstop for a session that
    was killed, crashed, or ended before its hook could run. Keyed on the
    session's recorded status rather than on the file's age, so it reclaims
    exactly what is provably finished instead of guessing from a clock.

    Bounded per call and best-effort: this runs on the paste path, where the
    operator is waiting, and tidying is never worth a failed upload.
    """
    try:
        with SessionLocal() as session:
            stale = session.execute(
                select(BridgeAttachment.trace_id)
                .join(Session, Session.trace_id == BridgeAttachment.trace_id)
                .where(BridgeAttachment.file_path.is_not(None),
                       Session.status == "ended")
                .distinct().limit(_SWEEP_BATCH)).scalars().all()
        for trace_id in stale:
            discard_files(trace_id)
    except Exception:  # noqa: BLE001 — housekeeping never fails a paste
        log.error("bridge_attachment_sweep_failed", exc_info=True)


def discard_files(trace_id: str) -> int:
    """Delete the materialized files for `trace_id`; keep the record.

    Safe by construction, and the asymmetry is the point: the file is the
    tmux tier's delivery vehicle, while the durable copy is the `prompt_images`
    blob the reader serves. Removing the file costs a re-read the agent has
    almost certainly already done, and costs the trace nothing.
    """
    removed = 0
    directory = _delivery_dir(trace_id)
    if directory.is_dir():
        for child in directory.iterdir():
            try:
                child.unlink()
                removed += 1
            except OSError:
                log.error("bridge_attachment_unlink_failed", path=str(child))
        try:
            directory.rmdir()
        except OSError:
            pass
    with SessionLocal() as session:
        session.execute(
            update(BridgeAttachment)
            .where(BridgeAttachment.trace_id == trace_id,
                   BridgeAttachment.file_path.is_not(None))
            .values(file_path=None))
        session.commit()
    if removed:
        log.write("bridge_attachment_files_discarded", trace_id=trace_id,
                  count=removed)
    return removed


def bind_prompt_span(trace_id: str, span_id: str, text: str) -> list[int]:
    """Settle every image `text` marks onto the prompt span that carried it.

    The staged row is claimed first and copied second, and the claim is the
    conditional write — so two ingests of the same prompt (a replay, a retry)
    cannot both produce a `prompt_images` row, and the loser simply does
    nothing. After this the bytes live where the trace UI already looks for
    them, which is what makes a composer paste render exactly like one typed
    into the terminal.

    The join is `(trace_id, marker idx)` over the rows this trace has already
    dispatched. It deliberately does NOT require the span's text to equal the
    delivered text: the harness re-renders a prompt on its way to the
    transcript (`text_digest`), so an equality test drops the image from the
    only span the reader ends up seeing — the live placeholder keeps it, the
    merge then retires the placeholder for the transcript's anchor, and the
    bytes are stranded under a span id nothing serves. The cost is that a
    trace mixing composer and terminal pastes can re-aim a dispatched image at
    a later prompt reusing its number; the number is per-trace and never
    reissued, so that needs both counters to collide.

    Returns the `[Image #N]` indices this call settled, which the caller puts
    on the span as `image_indices` — the attribute the reader uses to know an
    image is there at all.
    """
    indices = parse_markers(text)
    if not indices:
        return []
    bound: list[int] = []
    for row in by_indices(trace_id, indices, unclaimed_only=False):
        if not row.dispatched_sha:
            # Still a draft in the composer, so this marker is the harness's
            # own paste counter, not regin's — see `_mark_dispatched`.
            log.read("bridge_attachment_undispatched", trace_id=trace_id,
                     span_id=span_id, idx=row.idx)
            continue
        if _claim_and_copy(row, trace_id, span_id):
            bound.append(row.idx)
    if bound:
        log.write("bridge_attachment_bound", trace_id=trace_id,
                  span_id=span_id, indices=bound)
    return bound

