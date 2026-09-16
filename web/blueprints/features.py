"""The feature ledger on the web — the index, one feature, and declaring.

**Every read here is local.** The stage comes from `issue_cache` through
`lib.features.stage_source`, never from a fetch: a request the page fires on
mount is still a read, and a reloaded page is the poll `lib/tasks/links.py`
already refused. A tracker that cannot answer is therefore not an error on
this surface at all — it is `stage: null`, the HTTP analogue of
`regin feature ledger` exiting 0.

Reads are authenticated-any-role and writes are editor-or-better, which is the
split regin's roles actually encode: they guard against write-accidents, not
read-secrets. `GET /api/grades` already serves a viewer every session's
`cost_usd` plus fleet totals, so gating a per-feature *slice* of numbers a
viewer can already sum would protect nothing.

The refresh is a write despite being read-shaped: it spends someone else's API
quota and writes a cache two surfaces render from. Exactly the `GET …/links`
versus `POST …/refresh` split `tasks.py` draws.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request
from sqlmodel import text

from lib.auth import require_editor
from lib.features import attribution, index as index_module
from lib.features import ledger as ledger_module
from lib.features import spans
from lib.features import stage_source
from lib.orm import SessionLocal

features_bp = Blueprint("features", __name__)


def _known_trace(trace_id: str) -> bool:
    with SessionLocal() as session:
        return session.exec(text(
            "SELECT 1 FROM sessions WHERE trace_id = :t"
        ).bindparams(t=trace_id)).first() is not None


def _payload() -> dict:
    return request.get_json(silent=True) or {}


@features_bp.route("/api/features")
def api_features_index():
    """Every feature key regin knows.

    `q` is the only parameter. There is no cursor: the population grows only
    by an operator act — link, declare, blame — so it grows at the rate a
    human can scan, and a shared cursor is a link to a position that moves
    while a shared `?q=` is a link to a predicate.
    """
    page = index_module.page(q=request.args.get("q") or "")
    return jsonify(page.to_dict())


@features_bp.route("/api/features/<path:feature_key>")
def api_feature_ledger(feature_key):
    """One feature's ledger, computed entirely from local tables."""
    report = ledger_module.build(feature_key,
                                 stage_resolver=stage_source.from_cache)
    body = report.to_dict()
    # Beside the stage, never inside it: `Stage.to_dict()` is flat and stays
    # flat, and a null stage can carry no field — which is also why the two
    # are not orthogonal, since only a successful fetch writes the timestamp.
    body["stage_synced_at"] = stage_source.synced_at(feature_key)
    # Beside the timestamp, and the reason the timestamp has a slot to give
    # up: a tracker whose issues live in this database cannot lag the fact, so
    # `as of T` is meaningless there and the header says `live` instead.
    body["stage_live"] = stage_source.is_live(feature_key)
    return jsonify(body)


@features_bp.route("/api/features/<path:feature_key>/attribute",
                   methods=["POST"])
@require_editor
def api_feature_attribute(feature_key):
    """Declare that a session built this feature.

    The browser has no trace of its own to be the declarer, so this is always
    `recalled` — the rule `_provenance_for` already applied, not a fourth
    provenance. Origin is a different axis from identity.
    """
    trace_id = (_payload().get("trace_id") or "").strip()
    if not trace_id:
        return jsonify({"error": "trace_id is required"}), 400
    if not _known_trace(trace_id):
        return jsonify({
            "error": f"regin has no session {trace_id} — attributing one it "
                     "never captured would put a row under a feature that "
                     "does not exist"}), 404

    row = attribution.attribute(feature_key, trace_id,
                                primary=bool(_payload().get("is_primary")))
    spans.attributed(row)
    return jsonify({"feature_key": row.feature_key, "trace_id": row.trace_id,
                    "provenance": row.provenance,
                    "is_primary": bool(row.is_primary),
                    "round": row.round}), 201


@features_bp.route("/api/features/<feature_key>/attribute/<trace_id>",
                   methods=["DELETE"])
@require_editor
def api_feature_unattribute(feature_key, trace_id):
    """Withdraw a declaration.

    A session reaching this feature through the task lane has no row to
    remove, and says so rather than reporting a silent success: the assertion
    is the link on the task, and withdrawing it is an act on that task.
    """
    if attribution.unattribute(feature_key, trace_id):
        spans.unattributed(feature_key,
                           attribution.canonical_trace(trace_id))
        return jsonify({"feature_key": feature_key, "trace_id": trace_id,
                        "unattributed": True})
    reaches = any(row.feature_key == feature_key for row in
                  attribution.attributions_for_session(trace_id))
    detail = (" — it is attributed through a task link, which is withdrawn on"
              " the task, not here" if reaches else "")
    return jsonify({
        "error": f"{trace_id} has no declaration of {feature_key}{detail}"
    }), 404


@features_bp.route("/api/features/<path:feature_key>/stage/refresh",
                   methods=["POST"])
@require_editor
def api_feature_stage_refresh(feature_key):
    """Ask the tracker, once, and write what it says to the shared cache.

    A failure persists nothing — not the attempt, not a timestamp — so the
    caller can state that the stage beside it is unchanged and every other
    figure on the page is local anyway.
    """
    if stage_source.refresh(feature_key) is None:
        stage = stage_source.from_cache(feature_key)
        return jsonify({
            "error": "the tracker could not answer — it may be unreachable, "
                     "or it may hold no such issue; the stage shown is "
                     "unchanged and every other figure is local",
            "stage": stage.to_dict() if stage else None,
            "stage_synced_at": stage_source.synced_at(feature_key)}), 502
    stage = stage_source.from_cache(feature_key)
    return jsonify({"stage": stage.to_dict() if stage else None,
                    "stage_synced_at": stage_source.synced_at(feature_key)})
