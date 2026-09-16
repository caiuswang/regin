"""Composer image attachments — staging, tier delivery, and span binding.

The contract under test is the `[Image #N]` marker: it is what the composer
splices into the draft, what the delivered text carries to the agent, and what
names the staged bytes once the prompt's span lands. Every test here is about
that number surviving one of those hops.
"""

from __future__ import annotations

import base64
import pathlib
import struct
import zlib

import pytest
from sqlalchemy import select

from lib.agent_bridge import attachments
from lib.orm.engine import SessionLocal
from lib.orm.models import BridgeAttachment, PromptImage
from lib.auth import create_token


def _png(width: int = 4, height: int = 4) -> bytes:
    """A real, decodable PNG — the sniffer only reads magic bytes, but a
    fixture that is not actually an image invites a test that passes on
    a validator which never truly ran."""
    raw = b"".join(bytes([0]) + bytes([200, 40, 40] * width)
                   for _ in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return (struct.pack(">I", len(data)) + body
                + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def _staged_and_sent(trace_id: str, text: str, data: bytes | None = None) -> str:
    """Stage an image and dispatch it with `text`, as a real send does.

    Dispatch is not incidental: an undispatched row is deliberately unbindable,
    because a `[Image #N]` marker on its own cannot say whether the prompt came
    from this composer or from the operator's own terminal paste.
    """
    attachments.stage(trace_id, data or _png())
    attachments.for_sdk(trace_id, text)
    return text


@pytest.mark.parametrize("blob, expected", [
    (b"\x89PNG\r\n\x1a\n rest", "image/png"),
    (b"\xff\xd8\xff\xe0 rest", "image/jpeg"),
    (b"GIF89a rest", "image/gif"),
    (b"GIF87a rest", "image/gif"),
    (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "image/webp"),
])
def test_sniffs_the_supported_image_types(blob, expected):
    assert attachments.sniff_media_type(blob) == expected


@pytest.mark.parametrize("blob", [
    b"",
    b"<html>not an image",
    b"\x89PNG",                       # a truncated signature is not a PNG
    b"RIFF\x00\x00\x00\x00WAVEfmt ",  # RIFF alone is as often audio
])
def test_refuses_what_is_not_an_image(blob):
    assert attachments.sniff_media_type(blob) is None


def test_staging_numbers_images_from_one(tmp_db):
    first = attachments.stage("t-num", _png())
    second = attachments.stage("t-num", _png())
    assert (first["idx"], second["idx"]) == (1, 2)
    assert first["marker"] == "[Image #1]"
    assert second["marker"] == "[Image #2]"


def test_a_number_is_never_reused_after_its_image_is_claimed(tmp_db):
    """A claimed row's number still points at bytes from some delivered
    prompt; handing it to a new paste would re-aim that marker."""
    _staged_and_sent("t-reuse", "[Image #1] hi")
    attachments.bind_prompt_span("t-reuse", "span-1", "[Image #1] hi")
    assert attachments.stage("t-reuse", _png())["idx"] == 2


def test_a_non_image_is_refused_with_a_readable_reason(tmp_db):
    with pytest.raises(ValueError, match="not a supported image"):
        attachments.stage("t-bad", b"<html>")


def test_an_oversized_image_is_refused(tmp_db, monkeypatch):
    from lib.settings import settings
    monkeypatch.setattr(settings, "prompt_image_max_bytes", 32)
    with pytest.raises(ValueError, match="larger than"):
        attachments.stage("t-big", _png(64, 64))


def test_the_per_prompt_count_is_capped(tmp_db, monkeypatch):
    from lib.settings import settings
    monkeypatch.setattr(settings, "prompt_images_max_count", 2)
    attachments.stage("t-cap", _png())
    attachments.stage("t-cap", _png())
    with pytest.raises(ValueError, match="at most 2 images"):
        attachments.stage("t-cap", _png())


def test_the_sdk_tier_carries_the_bytes_themselves(tmp_db):
    data = _png()
    attachments.stage("t-sdk", data)
    images = attachments.for_sdk("t-sdk", "[Image #1] what is this?")
    assert len(images) == 1
    assert images[0]["media_type"] == "image/png"
    assert base64.b64decode(images[0]["data_b64"]) == data


def test_unmarked_text_carries_no_images(tmp_db):
    attachments.stage("t-unmarked", _png())
    assert attachments.for_sdk("t-unmarked", "no markers here") == []


def test_only_the_marked_images_travel(tmp_db):
    attachments.stage("t-subset", _png())
    attachments.stage("t-subset", _png())
    assert len(attachments.for_sdk("t-subset", "[Image #2] just that one")) == 1


def test_the_tmux_tier_writes_a_file_the_pane_can_open(tmp_db, tmp_path,
                                                       monkeypatch):
    data = _png()
    attachments.stage("t-tmux", data)
    monkeypatch.setattr(attachments, "_delivery_dir", lambda trace_id: tmp_path)
    delivered = attachments.for_tmux("t-tmux", "please look at [Image #1] here")
    # The path is what the agent opens; the marker is what the trace UI binds.
    assert "[Image #1]" in delivered
    written = pathlib.Path(
        delivered.split("@", 1)[1].split(" ", 1)[0])
    assert written.parent == tmp_path
    assert written.read_bytes() == data
    # Readable by the user running the pane, and by nobody else.
    assert oct(written.stat().st_mode)[-3:] == "600"


def test_binding_moves_the_bytes_onto_the_prompt_span(tmp_db):
    data = _png()
    text = _staged_and_sent("t-bind", "[Image #1] x", data)
    assert attachments.bind_prompt_span("t-bind", "span-9", text) == [1]
    with SessionLocal() as session:
        row = session.execute(
            select(PromptImage).where(PromptImage.trace_id == "t-bind")
        ).scalars().one()
    assert (row.prompt_span_id, row.idx, row.media_type) == ("span-9", 1,
                                                             "image/png")
    assert row.bytes_ == data


def test_a_replayed_prompt_does_not_duplicate_the_image(tmp_db):
    """Transcript replay re-posts the same span; the claim is the conditional
    write that makes the second pass a no-op."""
    text = _staged_and_sent("t-replay", "[Image #1] x")
    assert attachments.bind_prompt_span("t-replay", "span-1", text) == [1]
    assert attachments.bind_prompt_span("t-replay", "span-1", text) == []
    with SessionLocal() as session:
        rows = session.execute(
            select(PromptImage).where(PromptImage.trace_id == "t-replay")
        ).scalars().all()
    assert len(rows) == 1


def test_a_claimed_image_is_not_offered_to_a_later_prompt(tmp_db):
    _staged_and_sent("t-later", "[Image #1] first")
    attachments.bind_prompt_span("t-later", "span-1", "[Image #1] first")
    assert attachments.for_sdk("t-later", "[Image #1] second") == []


def test_binding_records_which_span_took_the_image(tmp_db):
    _staged_and_sent("t-claim", "[Image #1] x")
    attachments.bind_prompt_span("t-claim", "span-7", "[Image #1] x")
    with SessionLocal() as session:
        row = session.execute(
            select(BridgeAttachment).where(
                BridgeAttachment.trace_id == "t-claim")
        ).scalars().one()
    assert row.claimed_span_id == "span-7"


def test_text_with_no_markers_binds_nothing(tmp_db):
    attachments.stage("t-none", _png())
    assert attachments.bind_prompt_span("t-none", "span-1", "plain text") == []


# ── the upload route ────────────────────────────────────────────────────

def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def test_upload_stages_the_image_and_returns_its_marker(flask_client):
    resp = flask_client.post("/api/sessions/t-http/bridge-attachments",
                             json={"data_b64": _b64(_png())})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["marker"] == "[Image #1]"
    assert body["media_type"] == "image/png"


def test_upload_refuses_a_viewer(flask_client):
    """These bytes reach a live agent's context — the same reason
    `bridge-send` is editor-gated rather than merely authenticated."""
    viewer = {"Authorization":
              f"Bearer {create_token(2, 'viewer-tester', 'viewer')}"}
    resp = flask_client.post("/api/sessions/t-viewer/bridge-attachments",
                             json={"data_b64": _b64(_png())}, headers=viewer)
    assert resp.status_code == 403


def test_upload_refuses_an_anonymous_caller(anon_client):
    resp = anon_client.post("/api/sessions/t-anon/bridge-attachments",
                            json={"data_b64": _b64(_png())})
    assert resp.status_code == 401


@pytest.mark.parametrize("payload, expected", [
    ({}, "data_b64 required"),
    ({"data_b64": ""}, "data_b64 required"),
    ({"data_b64": "not base64!!"}, "not valid base64"),
    ({"data_b64": base64.b64encode(b"<html>").decode()},
     "not a supported image"),
])
def test_upload_rejects_bad_payloads(flask_client, payload, expected):
    resp = flask_client.post("/api/sessions/t-reject/bridge-attachments",
                             json=payload)
    assert resp.status_code == 400
    assert expected in resp.get_json()["error"]


def test_nothing_is_staged_when_the_upload_is_rejected(flask_client):
    flask_client.post("/api/sessions/t-clean/bridge-attachments",
                      json={"data_b64": base64.b64encode(b"<html>").decode()})
    with SessionLocal() as session:
        rows = session.execute(
            select(BridgeAttachment).where(
                BridgeAttachment.trace_id == "t-clean")
        ).scalars().all()
    assert rows == []


# ── the tmux tier, through the send route ───────────────────────────────

def test_the_tmux_send_carries_both_a_path_and_the_marker(flask_client,
                                                          tmp_path,
                                                          monkeypatch):
    """The two tokens answer two different questions: the path is what the
    agent opens, the marker is what binds the stored bytes to this prompt's
    span so the trace UI can draw the thumbnail."""
    from lib.agent_bridge import delivery
    from lib.settings import settings

    monkeypatch.setattr(settings.agent_bridge, "enabled", True)
    monkeypatch.setattr(attachments, "_delivery_dir", lambda trace_id: tmp_path)

    sent = []

    def _fake(trace_id, text, as_command=False):
        sent.append(text)
        return delivery.DeliveryResult(True, "delivered to %1")

    monkeypatch.setattr(delivery, "deliver", _fake)

    flask_client.post("/api/sessions/T-img/bridge-attachments",
                      json={"data_b64": _b64(_png())})
    flask_client.post("/api/sessions/T-img/bridge-send",
                      json={"text": "[Image #1] why is this misaligned?"})

    assert len(sent) == 1
    delivered = sent[0]
    assert "[Image #1]" in delivered
    assert str(tmp_path) in delivered
    assert delivered.endswith("why is this misaligned?")


def test_a_send_with_no_images_is_delivered_unchanged(flask_client,
                                                      monkeypatch):
    from lib.agent_bridge import delivery
    from lib.settings import settings

    monkeypatch.setattr(settings.agent_bridge, "enabled", True)
    sent = []

    def _fake(trace_id, text, as_command=False):
        sent.append(text)
        return delivery.DeliveryResult(True, "delivered to %1")

    monkeypatch.setattr(delivery, "deliver", _fake)
    flask_client.post("/api/sessions/T-plain/bridge-send",
                      json={"text": "just words"})
    assert sent == ["just words"]


def test_a_broken_staging_directory_still_delivers_the_text(flask_client,
                                                            monkeypatch):
    """Losing the picture is recoverable; losing the sentence the operator
    typed is not."""
    from lib.agent_bridge import delivery
    from lib.settings import settings

    monkeypatch.setattr(settings.agent_bridge, "enabled", True)

    def _explode(trace_id, text):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(attachments, "for_tmux", _explode)
    sent = []

    def _fake(trace_id, text, as_command=False):
        sent.append(text)
        return delivery.DeliveryResult(True, "delivered to %1")

    monkeypatch.setattr(delivery, "deliver", _fake)
    resp = flask_client.post("/api/sessions/T-broken/bridge-send",
                             json={"text": "[Image #1] look at this"})
    assert resp.get_json()["delivered"] is True
    assert sent == ["[Image #1] look at this"]


# ── the span ingest, which is what settles the image ────────────────────

def test_the_span_ingest_binds_the_image_it_finds_marked(flask_client):
    """The prompt reaches the agent before its span exists, so this is the
    first moment the bytes can learn which span they belong to."""
    flask_client.post("/api/sessions/T-span/bridge-attachments",
                      json={"data_b64": _b64(_png())})
    attachments.for_sdk("T-span", "[Image #1] why is this misaligned?")
    flask_client.post("/api/session-spans", json={
        "trace_id": "T-span", "span_id": "s-1", "name": "prompt",
        "start_time": "2026-09-01T00:00:00Z",
        "attributes": {"text": "[Image #1] why is this misaligned?"},
    })
    with SessionLocal() as session:
        row = session.execute(
            select(PromptImage).where(PromptImage.trace_id == "T-span")
        ).scalars().one()
    assert row.prompt_span_id == "s-1"


def test_a_span_that_is_not_a_prompt_binds_nothing(flask_client):
    flask_client.post("/api/sessions/T-tool/bridge-attachments",
                      json={"data_b64": _b64(_png())})
    flask_client.post("/api/session-spans", json={
        "trace_id": "T-tool", "span_id": "s-2", "name": "tool.Read",
        "start_time": "2026-09-01T00:00:00Z",
        "attributes": {"text": "[Image #1] not a prompt"},
    })
    with SessionLocal() as session:
        rows = session.execute(
            select(PromptImage).where(PromptImage.trace_id == "T-tool")
        ).scalars().all()
    assert rows == []


def test_a_failing_bind_never_costs_the_span(flask_client, monkeypatch):
    """A span is the trace's record of what happened; losing one to a
    thumbnail would be a bad trade."""
    def _explode(trace_id, span_id, text):
        raise RuntimeError("boom")

    monkeypatch.setattr(attachments, "bind_prompt_span", _explode)
    resp = flask_client.post("/api/session-spans", json={
        "trace_id": "T-safe", "span_id": "s-3", "name": "prompt",
        "start_time": "2026-09-01T00:00:00Z",
        "attributes": {"text": "[Image #1] hello"},
    })
    assert resp.status_code == 200
    assert resp.get_json()["ingested"] == 1


# ── the two `[Image #N]` namespaces ─────────────────────────────────────
#
# Claude Code numbers its OWN terminal pastes from 1, per session, out of
# `~/.claude/image-cache/` — a counter regin neither writes nor sees. So
# `[Image #1]` is genuinely ambiguous, and these tests pin the disambiguator.

def test_a_terminal_paste_cannot_swallow_an_unsent_composer_image(tmp_db):
    """The reported failure: an image staged in the composer but not yet sent
    must not be consumed by a prompt the operator typed in the terminal, which
    numbers its own paste `[Image #1]` too."""
    attachments.stage("t-ns", _png())          # staged, never sent
    # The operator submits an unrelated terminal prompt carrying its own image.
    assert attachments.bind_prompt_span("t-ns", "span-term",
                                        "[Image #1] a terminal paste") == []
    with SessionLocal() as session:
        row = session.execute(
            select(BridgeAttachment).where(
                BridgeAttachment.trace_id == "t-ns")
        ).scalars().one()
    assert row.claimed_span_id is None, "the composer's image was consumed"
    # And it is still deliverable when the operator finally sends the draft.
    assert len(attachments.for_sdk("t-ns", "[Image #1] mine")) == 1


def test_an_undispatched_image_is_never_bindable(tmp_db):
    attachments.stage("t-undisp", _png())
    assert attachments.bind_prompt_span("t-undisp", "s", "[Image #1] x") == []


def test_a_dispatched_image_binds_to_the_text_the_harness_re_rendered(tmp_db):
    """A slash command comes back from the transcript rebuilt as
    `<command-name> + ' ' + <command-args>`, each half stripped — so the span's
    text is not the text that went out. It is still the same prompt, and the
    only span the reader will see (the merge retires the live placeholder for
    it), so it has to carry the image."""
    _staged_and_sent("t-rerender", "/goal  [Image #1]  do the thing")
    assert attachments.bind_prompt_span(
        "t-rerender", "prompt-abc", "/goal [Image #1]  do the thing") == [1]


def test_the_binding_prompt_is_matched_regardless_of_surrounding_space(tmp_db):
    text = _staged_and_sent("t-ws", "[Image #1] x")
    assert attachments.bind_prompt_span("t-ws", "s", f"  {text}  ") == [1]


def test_a_second_upload_races_into_the_next_slot_not_a_500(tmp_db,
                                                            monkeypatch):
    """Both composers can be open on one trace, so the read-then-insert gap is
    reachable. The unique index is the real allocator; losing the race must
    cost a retry, not the upload."""
    real = attachments._next_idx
    calls = {"n": 0}

    def _stale(session, trace_id):
        # Hand the first two callers the same number, as a genuine race would.
        calls["n"] += 1
        return 1 if calls["n"] <= 2 else real(session, trace_id)

    attachments.stage("t-race", _png())
    monkeypatch.setattr(attachments, "_next_idx", _stale)
    second = attachments.stage("t-race", _png())
    assert second["idx"] == 2


def test_an_image_the_hook_already_stored_leaves_the_row_unclaimed(tmp_db):
    """If the transcript hook wrote `prompt_images` for this span+idx first,
    the copy cannot land — and consuming the staged row for a write that never
    happened would strand the bytes."""
    text = _staged_and_sent("t-dup", "[Image #1] x")
    with SessionLocal() as session:
        session.add(PromptImage(
            trace_id="t-dup", prompt_span_id="s", idx=1,
            media_type="image/png", bytes_=b"other", byte_size=5, sha256="x"))
        session.commit()
    assert attachments.bind_prompt_span("t-dup", "s", text) == []
    with SessionLocal() as session:
        row = session.execute(
            select(BridgeAttachment).where(
                BridgeAttachment.trace_id == "t-dup")
        ).scalars().one()
    assert row.claimed_span_id is None


# ── making it viewable ──────────────────────────────────────────────────
#
# Bytes in `prompt_images` are invisible on their own: the reader renders a
# thumbnail only for an index listed in the span's `image_indices`, so the
# bind has to announce what it settled or the feature silently does nothing.

def test_the_span_ingest_announces_the_bound_indices(flask_client):
    flask_client.post("/api/sessions/T-idx/bridge-attachments",
                      json={"data_b64": _b64(_png())})
    text = "[Image #1] look at this"
    attachments.for_sdk("T-idx", text)
    flask_client.post("/api/session-spans", json={
        "trace_id": "T-idx", "span_id": "s-idx", "name": "prompt",
        "start_time": "2026-09-01T00:00:00Z", "attributes": {"text": text},
    })
    from lib.orm.engine import get_connection
    import json as _json
    row = get_connection().execute(
        "SELECT attributes FROM session_spans WHERE span_id=?",
        ("s-idx",)).fetchone()
    assert _json.loads(row[0])["image_indices"] == [1]


def test_an_existing_index_from_the_terminal_is_not_dropped(flask_client):
    """A prompt can carry both a terminal paste and a composer one; keeping
    only ours would blank a thumbnail that has real bytes behind it."""
    flask_client.post("/api/sessions/T-mix/bridge-attachments",
                      json={"data_b64": _b64(_png())})
    text = "[Image #1] mine [Image #7] theirs"
    attachments.for_sdk("T-mix", text)
    flask_client.post("/api/session-spans", json={
        "trace_id": "T-mix", "span_id": "s-mix", "name": "prompt",
        "start_time": "2026-09-01T00:00:00Z",
        "attributes": {"text": text, "image_indices": [7]},
    })
    from lib.orm.engine import get_connection
    import json as _json
    row = get_connection().execute(
        "SELECT attributes FROM session_spans WHERE span_id=?",
        ("s-mix",)).fetchone()
    assert _json.loads(row[0])["image_indices"] == [1, 7]


def test_both_spans_of_one_prompt_carry_the_image(tmp_db):
    """A live placeholder and the transcript's own row coexist by design, and
    the reader may show either — so both must resolve the image."""
    text = _staged_and_sent("t-two", "[Image #1] x")
    assert attachments.bind_prompt_span("t-two", "promptlive-1", text) == [1]
    assert attachments.bind_prompt_span("t-two", "real-span-1", text) == [1]
    with SessionLocal() as session:
        spans = session.execute(
            select(PromptImage.prompt_span_id).where(
                PromptImage.trace_id == "t-two")).scalars().all()
    assert sorted(spans) == ["promptlive-1", "real-span-1"]


def test_files_never_land_inside_the_session_project(tmp_db, tmp_path,
                                                     monkeypatch):
    """regin's own tree ignores `.regin/`; no other project does, so writing
    there littered every repo but this one."""
    from lib.settings import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path / "regin-data")
    project = tmp_path / "someones-repo"
    project.mkdir()
    attachments.stage("t-shared", _png())
    delivered = attachments.for_tmux("t-shared", "[Image #1] look")
    assert str(project) not in delivered
    assert not (project / ".regin").exists()
    assert str(tmp_path / "regin-data" / "pasted") in delivered


def test_discarding_files_keeps_the_stored_image(tmp_db, tmp_path,
                                                 monkeypatch):
    """The file is the delivery vehicle; the blob is the record."""
    from lib.settings import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path / "d")
    data = _png()
    attachments.stage("t-disc", data)
    text = attachments.for_tmux("t-disc", "[Image #1] look")
    attachments.bind_prompt_span("t-disc", "s-disc", text)
    path = pathlib.Path(text.split("@", 1)[1].split(" ", 1)[0])
    assert path.is_file()

    assert attachments.discard_files("t-disc") == 1
    assert not path.exists()
    with SessionLocal() as session:
        row = session.execute(
            select(PromptImage).where(PromptImage.trace_id == "t-disc")
        ).scalars().one()
    assert row.bytes_ == data          # the reader still serves it
    staged = attachments.by_indices("t-disc", [1], unclaimed_only=False)
    assert staged[0].file_path is None  # and the row no longer claims a file


def test_discarding_a_trace_with_no_files_is_a_no_op(tmp_db, tmp_path,
                                                     monkeypatch):
    from lib.settings import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path / "d")
    assert attachments.discard_files("t-empty") == 0


def test_each_reference_lands_at_its_own_marker(tmp_db, tmp_path, monkeypatch):
    """In the terminal an image appears where the cursor is, and the sentence
    was composed around that spot — hoisting every path to the front moves the
    picture away from the words pointing at it."""
    monkeypatch.setattr(attachments, "_delivery_dir", lambda trace_id: tmp_path)
    attachments.stage("t-pos", _png())
    attachments.stage("t-pos", _png())
    delivered = attachments.for_tmux(
        "t-pos", "before [Image #1] middle [Image #2] after")
    assert delivered.startswith("before @")
    assert delivered.endswith("after")
    # Each marker keeps its position, with its own reference immediately before.
    order = [chunk for chunk in delivered.split() if chunk.startswith("@")
             or chunk.startswith("[Image")]
    assert order[1] == "[Image" or "[Image #1]" in delivered
    assert delivered.index("[Image #1]") < delivered.index("middle")
    assert delivered.index("middle") < delivered.index("[Image #2]")


def test_a_marker_with_no_staged_image_is_left_alone(tmp_db, tmp_path,
                                                     monkeypatch):
    monkeypatch.setattr(attachments, "_delivery_dir", lambda trace_id: tmp_path)
    attachments.stage("t-gap", _png())
    delivered = attachments.for_tmux("t-gap", "[Image #1] a [Image #9] b")
    assert delivered.count("@") == 1
    assert "[Image #9] b" in delivered


def test_a_replayed_span_keeps_its_image_indices(flask_client):
    """The transcript re-posts spans it has already sent, and the ingest
    upserts attributes wholesale — so a stamp derived from what the bind
    returned would blank itself on the second pass."""
    import json as _json
    from lib.orm.engine import get_connection

    flask_client.post("/api/sessions/T-replay2/bridge-attachments",
                      json={"data_b64": _b64(_png())})
    text = "[Image #1] look"
    attachments.for_sdk("T-replay2", text)
    span = {"trace_id": "T-replay2", "span_id": "s-r", "name": "prompt",
            "start_time": "2026-09-01T00:00:00Z", "attributes": {"text": text}}
    flask_client.post("/api/session-spans", json=span)
    flask_client.post("/api/session-spans", json=span)   # the replay

    row = get_connection().execute(
        "SELECT attributes FROM session_spans WHERE span_id=?", ("s-r",)).fetchone()
    assert _json.loads(row[0])["image_indices"] == [1]


# ── reclaiming the files ────────────────────────────────────────────────

def test_session_end_reclaims_the_files(tmp_db, tmp_path, monkeypatch):
    from hook_manager.core import HookPayload
    from hook_manager.handlers import session_lifecycle
    from lib.settings import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path / "d")

    attachments.stage("t-end", _png())
    text = attachments.for_tmux("t-end", "[Image #1] look")
    path = pathlib.Path(text.split("@", 1)[1].split(" ", 1)[0])
    assert path.is_file()

    session_lifecycle.handle_end(HookPayload(
        event="SessionEnd", session_id="t-end", raw={"reason": "logout"}))
    assert not path.exists()


def test_a_failing_sweep_never_breaks_session_end(tmp_db, monkeypatch):
    from hook_manager.core import HookPayload
    from hook_manager.handlers import session_lifecycle

    def _explode(trace_id):
        raise OSError("disk gone")

    monkeypatch.setattr(attachments, "discard_files", _explode)
    resp = session_lifecycle.handle_end(HookPayload(
        event="SessionEnd", session_id="t-boom", raw={"reason": "logout"}))
    assert resp is not None


def test_the_backstop_reclaims_a_session_that_never_ended_cleanly(
        tmp_db, tmp_path, monkeypatch):
    """A killed session fires no SessionEnd; the sweep keys on the recorded
    status rather than on how old the file is."""
    from lib.orm.models import Session as SessionRow
    from lib.settings import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path / "d")

    attachments.stage("t-killed", _png())
    text = attachments.for_tmux("t-killed", "[Image #1] look")
    path = pathlib.Path(text.split("@", 1)[1].split(" ", 1)[0])
    assert path.is_file()

    with SessionLocal() as session:
        session.add(SessionRow(trace_id="t-killed", status="ended",
                               started_at="2026-09-01T00:00:00",
                               last_seen="2026-09-01T00:00:00"))
        session.commit()

    attachments.stage("t-other", _png())   # any later paste triggers the sweep
    assert not path.exists()


def test_the_backstop_leaves_a_live_session_alone(tmp_db, tmp_path,
                                                  monkeypatch):
    from lib.orm.models import Session as SessionRow
    from lib.settings import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path / "d")

    attachments.stage("t-live", _png())
    text = attachments.for_tmux("t-live", "[Image #1] look")
    path = pathlib.Path(text.split("@", 1)[1].split(" ", 1)[0])

    with SessionLocal() as session:
        session.add(SessionRow(trace_id="t-live", status="active",
                               started_at="2026-09-01T00:00:00",
                               last_seen="2026-09-01T00:00:00"))
        session.commit()

    attachments.stage("t-other2", _png())
    assert path.is_file(), "a running session still needs its file"

