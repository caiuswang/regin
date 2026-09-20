"""Screenshots attached to a defect — storage, the routes, and the proposed
index.

A bug report's whole evidence used to be the reporter's sentence: the browser
form took text only, so the picture the sentence was describing went into a
chat message the ledger never saw.

Two things are specific to this owner and are what these tests pin. The bytes
are **evidence, not model input** — nothing delivers them to an agent, unlike a
task's or a feature's images. And the row is **deleted with its defect**,
because a screenshot has no meaning without the report it evidences.
"""

from __future__ import annotations

import base64

import pytest
from sqlalchemy import text
from sqlmodel import select

from lib.auth import create_token
from lib.defects import images as defect_images
from lib.orm import SessionLocal
from lib.orm.models.defects import DefectImage

EDITOR = {"Authorization": f"Bearer {create_token(2, 'ed', 'editor')}"}
VIEWER = {"Authorization": f"Bearer {create_token(3, 'vw', 'viewer')}"}

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
GIF = b"GIF89a" + b"\x00" * 16
NOT_AN_IMAGE = b"<svg>nope</svg>"


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _open(client, feature_id, symptom="the gutter folds the newest span"):
    resp = client.post("/api/defects",
                       json={"symptom": symptom, "feature_id": feature_id},
                       headers=EDITOR)
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()["defect"]["id"]


def _attach(client, defect_id, data=PNG, headers=EDITOR, **extra):
    return client.post(f"/api/defects/{defect_id}/images",
                       json={"data_b64": _b64(data), **extra}, headers=headers)


# ── attaching ────────────────────────────────────────────────

def test_a_defect_carries_the_screenshot_it_was_reported_with(
        flask_client, feature_row):
    defect_id = _open(flask_client, feature_row().id)

    first = _attach(flask_client, defect_id, PNG)
    second = _attach(flask_client, defect_id, GIF)

    assert first.status_code == 201, first.get_json()
    assert first.get_json()["image"]["idx"] == 1
    assert second.get_json()["image"]["idx"] == 2
    assert second.get_json()["image"]["media_type"] == "image/gif"


def test_the_row_carries_its_images_on_both_the_read_and_the_list(
        flask_client, feature_row):
    feature = feature_row()
    defect_id = _open(flask_client, feature.id)
    _attach(flask_client, defect_id, PNG)

    one = flask_client.get(f"/api/defects/{defect_id}", headers=VIEWER)
    listing = flask_client.get(f"/api/defects?feature_id={feature.id}",
                               headers=VIEWER)

    assert [i["idx"] for i in one.get_json()["defect"]["images"]] == [1]
    row = listing.get_json()["items"][0]
    # The metadata, never the bytes: a page of 50 reports must not carry a
    # megabyte of base64.
    assert [i["idx"] for i in row["images"]] == [1]
    assert "bytes" not in row["images"][0]


def test_the_bytes_come_back_under_the_media_type_they_were_sniffed_as(
        flask_client, feature_row):
    defect_id = _open(flask_client, feature_row().id)
    _attach(flask_client, defect_id, GIF)

    resp = flask_client.get(f"/api/defects/{defect_id}/images/1",
                            headers=VIEWER)

    assert resp.status_code == 200
    assert resp.mimetype == "image/gif"
    assert resp.data == GIF


def test_something_that_is_not_an_image_is_refused_with_its_reason(
        flask_client, feature_row):
    defect_id = _open(flask_client, feature_row().id)

    resp = _attach(flask_client, defect_id, NOT_AN_IMAGE)

    assert resp.status_code == 400
    assert "not a supported image" in resp.get_json()["error"]


def test_attaching_to_a_defect_that_is_not_there_is_a_404(flask_client):
    assert _attach(flask_client, 9999).status_code == 404


def test_a_viewer_cannot_attach_but_can_read(flask_client, feature_row):
    defect_id = _open(flask_client, feature_row().id)
    _attach(flask_client, defect_id, PNG)

    assert _attach(flask_client, defect_id, GIF,
                   headers=VIEWER).status_code == 403
    assert flask_client.get(f"/api/defects/{defect_id}/images/1",
                            headers=VIEWER).status_code == 200


# ── the proposed index ───────────────────────────────────────

def test_a_composer_gets_the_index_its_prose_already_names(
        flask_client, feature_row):
    """The reason `idx` is a request field at all.

    The reporter pastes a screenshot into a form for a defect that does not
    exist yet, and the browser writes the reference against an index it
    allocated itself. The upload that finally lands the bytes has to be able to
    ask for that index — including after the first of two held images was
    removed, which is when the naive "next free" answer stops matching.
    """
    defect_id = _open(flask_client, feature_row().id)

    resp = _attach(flask_client, defect_id, PNG, idx=2)

    assert resp.get_json()["image"]["idx"] == 2


def test_a_proposal_for_a_taken_index_falls_back_rather_than_refusing(
        flask_client, feature_row):
    """The allocator stays total.

    A refusal here would strand bytes the reporter can see on screen. The
    reference in the prose is what goes stale instead — and a stale reference
    renders as visible text, not as a broken picture.
    """
    defect_id = _open(flask_client, feature_row().id)
    _attach(flask_client, defect_id, PNG)

    resp = _attach(flask_client, defect_id, GIF, idx=1)

    assert resp.status_code == 201
    assert resp.get_json()["image"]["idx"] == 2


@pytest.mark.parametrize("proposed", [0, -3, "2", True, None])
def test_a_proposal_that_is_not_a_positive_int_attaches_at_the_next_free_index(
        flask_client, feature_row, proposed):
    """Malformed means "no opinion", never "refuse".

    The picture is the point; an index the client got wrong is not worth
    losing it over.
    """
    defect_id = _open(flask_client, feature_row().id)

    resp = _attach(flask_client, defect_id, PNG, idx=proposed)

    assert resp.status_code == 201, resp.get_json()
    assert resp.get_json()["image"]["idx"] == 1


# ── removing, and the cascade ────────────────────────────────

def test_a_freed_index_below_the_high_water_mark_is_not_reissued(
        flask_client, feature_row):
    """The half of the `idx` discipline that actually holds.

    `_next_idx` counts from `max(idx) + 1` over the surviving rows, so a gap
    punched in the middle stays a gap and the reference that named it keeps
    reading as detached. The high-water case does NOT hold — see the test
    below.
    """
    defect_id = _open(flask_client, feature_row().id)
    for _ in range(3):
        _attach(flask_client, defect_id, PNG)

    removed = flask_client.delete(f"/api/defects/{defect_id}/images/2",
                                  headers=EDITOR)
    again = _attach(flask_client, defect_id, GIF)

    assert removed.status_code == 200
    assert again.get_json()["image"]["idx"] == 4
    assert flask_client.delete(f"/api/defects/{defect_id}/images/2",
                               headers=EDITOR).status_code == 404


def test_removing_the_highest_image_does_reissue_its_index(flask_client,
                                                           feature_row):
    """Pinned as the shape it is, not as the shape it should be.

    Nothing on the row remembers a freed high-water index, so detaching the
    last image and attaching another hands the new bytes the index the old
    ones had — and an `![](image:2)` already in some prose then resolves to a
    different picture with nothing to say so. Shared by all three `ImageSet`
    owners; closing it needs a per-owner counter the tables do not carry.
    """
    defect_id = _open(flask_client, feature_row().id)
    _attach(flask_client, defect_id, PNG)
    _attach(flask_client, defect_id, PNG)

    flask_client.delete(f"/api/defects/{defect_id}/images/2", headers=EDITOR)
    again = _attach(flask_client, defect_id, GIF)

    assert again.get_json()["image"]["idx"] == 2


def test_deleting_the_defect_takes_its_evidence_with_it(flask_client,
                                                        feature_row):
    """ON DELETE CASCADE, unlike a feature's mockup.

    A feature's image outlives the slices cut from it. A defect's screenshot
    is *of* the report — orphaning it would leave bytes nothing can name.
    """
    defect_id = _open(flask_client, feature_row().id)
    _attach(flask_client, defect_id, PNG)

    with SessionLocal() as session:
        session.execute(text("PRAGMA foreign_keys = ON"))
        session.execute(text("DELETE FROM defects WHERE id = :i"),
                        {"i": defect_id})
        session.commit()

    with SessionLocal() as session:
        left = session.exec(
            select(DefectImage).where(DefectImage.defect_id == defect_id)
        ).all()
    assert left == []


def test_the_store_helpers_read_what_the_routes_wrote(flask_client,
                                                      feature_row):
    defect_id = _open(flask_client, feature_row().id)
    _attach(flask_client, defect_id, PNG)

    assert [i["idx"] for i in defect_images.list_for(defect_id)] == [1]
    assert defect_images.get_bytes(defect_id, 1) == (PNG, "image/png")
    assert defect_images.summaries_for([defect_id])[defect_id][0]["idx"] == 1
