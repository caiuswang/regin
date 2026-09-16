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
from lib.defects import store

defects_bp = Blueprint("defects", __name__)

_FIELDS = {"symptom", "feature_key", "suspect_paths"}

SCOPE_ALL = "all"
SCOPE_UNATTRIBUTED = "unattributed"
SCOPE_FEATURE = "feature"
SCOPE_FIX_ITEM = "fix-item"


def _serialize(row) -> dict:
    return {
        "id": row.id, "feature_key": row.feature_key, "symptom": row.symptom,
        "status": row.status, "round": row.round,
        "blamed_against_state": row.blamed_against_state,
        "missing_check": row.missing_check,
        "suspect_paths": json.loads(row.suspect_paths_json)
        if row.suspect_paths_json else [],
        "fix_provider": row.fix_provider,
        "fix_external_key": row.fix_external_key,
        "opened_at": row.opened_at, "closed_at": row.closed_at,
    }


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


def _fix_item_ref(text: str):
    """The `(provider, key)` a fix-item query names, or None.

    Whatever the tracker recognizes is tried first — a bare `CAI-241`, a
    pasted issue URL, `regin:42` — so this route accepts the same spellings
    the *write* does. `<provider>:<key>` stays as the fallback it documented.

    The order matters and is the bug: a native key contains a colon of its
    own, so partitioning first read `regin:42` as provider `regin`, key `42`
    and answered "nothing points at this item" for rows that do.
    """
    from lib.defects import tracker  # noqa: PLC0415  (circular at import time)
    try:
        ref = tracker.name_ref(text)
    except tracker.TrackerUnavailable:
        provider, _, external_key = text.partition(":")
        return (provider, external_key) if provider and external_key else None
    return ref.provider, ref.key


def _listing(rows, scope: str):
    return jsonify({"items": [_serialize(row) for row in rows],
                    "scope": scope})


@defects_bp.route("/api/defects", methods=["GET"])
@require_auth
def api_list_defects():
    """The ledger, in one of four scopes.

    `all` is the default, and that is the whole repair: unattributed used to
    be, so a defect reported against a feature — the shape every report from a
    task page has — belonged to no scope any surface opened. It was written,
    it was returned, and it was then unreadable. Unattributed keeps its own
    name because it is what the `/features` counter links to, but it is one
    predicate over the ledger now, not the way in.
    """
    feature = (request.args.get("feature") or "").strip()
    fix_item = (request.args.get("fix_item") or "").strip()
    if fix_item:
        named = _fix_item_ref(fix_item)
        if named is None:
            return jsonify({"error": "fix_item must name a tracker item, or "
                                     "be spelled <provider>:<key>"}), 400
        return _listing(store.by_fix_item(*named), SCOPE_FIX_ITEM)
    if feature:
        return _listing(store.for_feature(feature), SCOPE_FEATURE)
    scope = (request.args.get("scope") or SCOPE_ALL).strip()
    if scope == SCOPE_UNATTRIBUTED:
        return _listing(store.unattributed(), SCOPE_UNATTRIBUTED)
    if scope != SCOPE_ALL:
        return jsonify({"error": f"unknown scope: {scope}"}), 400
    return _listing(store.every(), SCOPE_ALL)


@defects_bp.route("/api/defects", methods=["POST"])
@require_editor
def api_open_defect():
    payload = request.get_json(silent=True) or {}
    problem = _bad_request(payload)
    if problem:
        return jsonify({"error": problem}), 400
    feature_key = (payload.get("feature_key") or "").strip() or None
    try:
        recorded = store.record_reporting(
            payload["symptom"], feature_key=feature_key,
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
    feature_key = (payload.get("feature_key") or "").strip()
    if not feature_key:
        return jsonify({"error": "attribution needs a feature key"}), 400
    return _write(defect_id, lambda: store.attribute(defect_id, feature_key))


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
