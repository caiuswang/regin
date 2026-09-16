"""The defect ledger on the web — `regin defects` behind the JWT.

A surface over `lib.defects.store`, never a second rulebook. Every rule that
makes the count trustworthy — dedup, the closed-twin refusal, write-once
blame, `close` demanding the check that would have caught it — is raised by
the store; this module's whole job is to map those refusals onto status codes
and the row onto JSON. It holds no `SessionLocal()` of its own, which is what
keeps that true: a blueprint that cannot reach the table cannot grow a rule.

The store speaks one exception for every refusal, so the code a route returns
is decided *before* the call, from the shape of the request:

  * a field the store would reject as blank is rejected here as `400`, the
    posture `POST /api/defects` already takes for a missing symptom;
  * a row that is not there is `404`, read by asking the store for it;
  * whatever is left is the ledger refusing a well-formed act — the merge
    remedy, the already-open round — and that is `409`.

`session_id` is never read from the body. A defect opened on a page belongs to
no trace: the browser has no session of its own to be the reporter, and letting
it name one would let it attribute a report to a trace it never ran.
"""

from __future__ import annotations

import json

from flask import Blueprint, jsonify, request

from lib.auth import require_auth, require_editor
from lib.defects import query as defect_query
from lib.defects import store
from lib.utils.pagination import clamp_page, clamp_size

defects_bp = Blueprint("defects", __name__)

_FIELDS = {"symptom", "feature_id", "suspect_paths"}

SCOPE_ALL = defect_query.SCOPE_ALL
SCOPE_UNATTRIBUTED = defect_query.SCOPE_UNATTRIBUTED
SCOPE_FEATURE = defect_query.SCOPE_FEATURE
SCOPE_FIX_ITEM = defect_query.SCOPE_FIX_ITEM


def _view(row, feature_title, blamed_event_kind) -> dict:
    return {
        "id": row.id, "feature_id": row.feature_id, "symptom": row.symptom,
        # The feature's own title, never a task's: the two answer different
        # questions and diverge, and no surface falls back from one to the
        # other.
        "feature_title": feature_title,
        "status": row.status, "round": row.round,
        # What blame was assigned against, read off the event it points at. An
        # escape is `blamed_event_kind == 'shipped'` — one comparison, and no
        # "we never looked" reading to render as a gap.
        "blamed_event_kind": blamed_event_kind,
        "missing_check": row.missing_check,
        "suspect_paths": json.loads(row.suspect_paths_json)
        if row.suspect_paths_json else [],
        "fix_task_id": row.fix_task_id,
        "opened_at": row.opened_at, "updated_at": row.updated_at,
        "closed_at": row.closed_at,
    }


def _serialize(row) -> dict:
    """One defect, for the response to a write.

    Two reads for one row, and deliberately not the shape the list uses: this
    is a single row the caller just changed, so there is no loop for the cost
    to multiply through. The list reads its two extra facts off the row.
    """
    return _view(row, _feature_title(row), _blamed_kind(row))


def _feature_title(row):
    if row.feature_id is None:
        return None
    from lib.features import store as feature_store  # noqa: PLC0415

    feature = feature_store.get(row.feature_id)
    return feature.title if feature else None


def _blamed_kind(row):
    """The kind of the event this defect is blamed against, or None."""
    if row.blamed_event_id is None:
        return None
    from lib.orm import SessionLocal  # noqa: PLC0415
    from lib.orm.models.features import FeatureEvent  # noqa: PLC0415

    with SessionLocal() as session:
        event = session.get(FeatureEvent, row.blamed_event_id)
    return event.kind if event else None


def _bad_request(payload: dict):
    unknown = sorted(set(payload) - _FIELDS)
    if unknown:
        return f"unknown field(s): {', '.join(unknown)}"
    if not isinstance(payload.get("symptom"), str) \
            or not payload["symptom"].strip():
        return "symptom is required"
    paths = payload.get("suspect_paths", [])
    if not isinstance(paths, list) or any(not isinstance(p, str) for p in paths):
        return "suspect_paths must be a list of strings"
    return None


@defects_bp.route("/api/defects", methods=["GET"])
@require_auth
def api_list_defects():
    """One bounded page of the ledger, under one narrowing and one order.

    The four scopes are four predicates over the same read now, not four
    readers — which is what retires the reported bug. `all` came back
    `id DESC` and every other scope `id ASC`, so pressing a scope pill
    silently reversed the list; direction is a property of the sort, the sort
    is a parameter, and no scope holds a private opinion about it.

    `all` stays the default. Unattributed used to be, so a defect reported
    against a feature — the shape every report from a task page has —
    belonged to no scope any surface opened. It keeps its own name because it
    is what the `/features` counter links to.
    """
    try:
        query = defect_query.from_args(request.args)
    except defect_query.QueryError as exc:
        return jsonify({"error": str(exc)}), 400
    result = store.listing(query,
                           page=clamp_page(request.args.get("page")),
                           size=clamp_size(request.args.get("size")))
    body = result.to_envelope()
    body["items"] = [_view(row.defect, row.feature_title,
                           row.blamed_event_kind) for row in result.items]
    # The scope and the sort are echoed because the controls above the list
    # have to be able to render what the server actually applied — a sort key
    # the server fell back on, shown as the one that was asked for, is a
    # control that lies about its own list.
    body["scope"] = query.scope
    body["sort"] = str(query.sort)
    return jsonify(body)


@defects_bp.route("/api/defects/<int:defect_id>", methods=["GET"])
@require_auth
def api_read_defect(defect_id: int):
    """One defect by id — what a permalink resolves to.

    The list is a predicate and a page position; neither survives being sent
    to someone else. A row needs an address of its own, and an address needs
    a read that does not depend on which page the row happened to be on.
    """
    try:
        row = store.get(defect_id)
    except store.DefectError:
        return jsonify({"error": f"no defect {defect_id}"}), 404
    return jsonify({"defect": _serialize(row)})


@defects_bp.route("/api/defects", methods=["POST"])
@require_editor
def api_open_defect():
    payload = request.get_json(silent=True) or {}
    problem = _bad_request(payload)
    if problem:
        return jsonify({"error": problem}), 400
    feature_id = payload.get("feature_id")
    try:
        recorded = store.record_reporting(
            payload["symptom"], feature_id=feature_id,
            suspect_paths=payload.get("suspect_paths", []))
    except store.DefectError as exc:
        return jsonify({"error": str(exc)}), 409
    return jsonify({"outcome": recorded.outcome,
                    "defect": _serialize(recorded.defect)}), \
        200 if recorded.deduped else 201


def _missing(defect_id: int) -> bool:
    try:
        store.get(defect_id)
    except store.DefectError:
        return True
    return False


def _write(defect_id: int, act):
    """Run one store verb, mapping its refusal onto a code.

    `404` is decided by asking the store whether the row exists rather than by
    reading the refusal's wording: matching on a message would make the
    sentence the store writes for a human into an API contract.
    """
    if _missing(defect_id):
        return jsonify({"error": f"no defect {defect_id}"}), 404
    try:
        row = act()
    except store.DefectError as exc:
        return jsonify({"error": str(exc)}), 409
    return jsonify({"defect": _serialize(row)}), 200


@defects_bp.route("/api/defects/<int:defect_id>/attribute", methods=["POST"])
@require_editor
def api_attribute_defect(defect_id: int):
    """Name the feature that caused the bug — a human's assertion.

    Nothing here proposes a key from the defect's suspect paths, its session
    or its cost. An unattributed defect stays unattributed until somebody
    types one: a wrong parent inflates one feature's rounds and exonerates
    another, which is corrupted data that looks fine.
    """
    payload = request.get_json(silent=True) or {}
    feature_id = payload.get("feature_id")
    if not feature_id:
        return jsonify({"error": "attribution needs a feature id"}), 400
    return _write(defect_id, lambda: store.attribute(defect_id, feature_id))


@defects_bp.route("/api/defects/<int:defect_id>/close", methods=["POST"])
@require_editor
def api_close_defect(defect_id: int):
    payload = request.get_json(silent=True) or {}
    missing_check = (payload.get("missing_check") or "").strip()
    if not missing_check:
        return jsonify({"error": "closing a defect requires naming the check "
                                 "that would have caught it"}), 400
    return _write(defect_id,
                  lambda: store.close(defect_id, missing_check=missing_check))


@defects_bp.route("/api/defects/<int:defect_id>/reopen", methods=["POST"])
@require_editor
def api_reopen_defect(defect_id: int):
    """Record that we claimed this fixed and were wrong. This is the round."""
    return _write(defect_id, lambda: store.reopen(defect_id))
