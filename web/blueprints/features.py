"""The feature ledger on the web — the index, one feature, and declaring.

**Every read here is local**, including the stage: it is the feature's last
`feature_events` row plus a count of escapes. That used to need a cached
tracker snapshot, so a request the page fired on mount was a read that could
turn into a poll against someone else's API, and a feature nobody had fetched
had no stage at all. There is nothing left on this surface that a remote can
fail to answer.

Reads are authenticated-any-role and writes are editor-or-better, which is the
split regin's roles actually encode: they guard against write-accidents, not
read-secrets. `GET /api/grades` already serves a viewer every session's
`cost_usd` plus fleet totals, so gating a per-feature *slice* of numbers a
viewer can already sum would protect nothing.

The one exception is `POST …/refresh` on a remotely-tracked feature: a write
despite being read-shaped, because it spends someone else's API quota and
appends to an append-only table. Native features have no such route at all.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request
from sqlmodel import text

from lib.auth import require_editor
from lib.features import attribution, index as index_module
from lib.features import ledger as ledger_module
from lib.features import spans
from lib.features import store as feature_store
from lib.features import sync as sync_module
from lib.orm import SessionLocal
from lib.tasks.integrations import IntegrationError
from lib.tasks.query import FilterRejected
from lib.utils.pagination import clamp_page, clamp_size

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
    """One offset page of the feature index, under the URL's own filters.

    Offset rather than a cursor: the population grows by an operator act —
    link, declare, blame — not by ingest, so a page number still names the
    same rows on the next read, and both "page 7" and a sortable column are
    things a cursor cannot express.

    An unknown *facet value* is a 400 naming the parameter, while an unknown
    *sort key* falls back to the default. A filter that silently vanished
    would show a list contradicting the controls above it; a sort that did
    is the same rows in another order.
    """
    try:
        filters = index_module.parse_filters(request.args)
    except FilterRejected as exc:
        return jsonify({"error": str(exc), "field": exc.field}), 400
    page = index_module.page(
        filters=filters,
        page_index=clamp_page(request.args.get("page")),
        size=clamp_size(request.args.get("size")))
    return jsonify(page.to_dict())


@features_bp.route("/api/features", methods=["POST"])
@require_editor
def api_feature_create():
    """Declare a feature, or adopt a tracker issue as one.

    Two shapes because they have two writers of the title: a native feature's
    is typed here, and an adopted one's comes from the fetch — the operator is
    naming an issue that already has a name, and a second one for it would be
    two writers on one field.

    A feature may be created with no task at all. It can be shipped, blamed
    and reported on; it simply has nothing for an agent to run.
    """
    payload = _payload()
    ref = (payload.get("ref") or "").strip()
    try:
        if ref:
            row = sync_module.adopt(ref, provider_id=(
                payload.get("provider") or "").strip())
        else:
            row = feature_store.create((payload.get("title") or ""),
                                       body=payload.get("body"))
    except (feature_store.FeatureError, IntegrationError) as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(ledger_module.build(row.id).to_dict()), 201


def _ledger_body(feature_id: int) -> dict:
    """The ledger every route that returns one serves.

    Shared rather than inlined per route: a PATCH that answered with a thinner
    body than the GET made the page it re-seated worse than the one it
    replaced — `stage_live` came back absent, `!undefined` read as true, and a
    refresh-from-remote control appeared on a native feature.
    """
    body = ledger_module.build(feature_id).to_dict()
    # Beside the stage rather than inside it: a remote's freshness is a fact
    # about the fetch, not about where the feature stands, and a native
    # feature has no "as of T" at all because its stage is derived here.
    feature = feature_store.require(feature_id)
    body["synced_at"] = feature.synced_at
    body["stage_live"] = not sync_module.is_remote(feature)
    return body


@features_bp.route("/api/features/<int:feature_id>")
def api_feature_ledger(feature_id):
    """One feature's ledger, computed entirely from local tables."""
    try:
        return jsonify(_ledger_body(feature_id))
    except feature_store.FeatureError:
        return jsonify({"error": f"no feature {feature_id}"}), 404


@features_bp.route("/api/features/<int:feature_id>", methods=["PATCH"])
@require_editor
def api_feature_edit(feature_id):
    """Edit the feature's own title, spec or project.

    `project_id` is keyed on PRESENCE, not truthiness: an explicit `null`
    unfiles the feature and an absent key leaves it alone, because "move this
    to no project" and "leave the project alone" are different requests and
    collapsing them is how an unrelated title edit silently unfiles a feature.
    """
    payload = _payload()
    try:
        if "title" in payload:
            feature_store.rename(feature_id, payload["title"])
        if "body" in payload:
            feature_store.set_body(feature_id, payload["body"])
        if "project_id" in payload:
            feature_store.set_project(feature_id, payload["project_id"])
    except feature_store.FeatureError as exc:
        return jsonify({"error": str(exc),
                        "field": getattr(exc, "field", None)}), 400
    return jsonify(_ledger_body(feature_id))


@features_bp.route("/api/features/<int:feature_id>/ship", methods=["POST"])
@require_editor
def api_feature_ship(feature_id):
    """The done-claim. This is the boundary later defects are blamed against.

    Filing a defect never walks it back: a board that flaps is a board nobody
    trusts, and the escape is counted from the defect's own pointer at the
    event this appends.
    """
    try:
        feature_store.ship(feature_id)
    except feature_store.FeatureError as exc:
        return jsonify({"error": str(exc)}), 404
    return jsonify(_ledger_body(feature_id))


@features_bp.route("/api/features/<int:feature_id>/abandon", methods=["POST"])
@require_editor
def api_feature_abandon(feature_id):
    """Take a feature down. Costs no round, and there is no delete.

    A feature's events are the only source of its rounds and its escapes, so
    removing one would not be a cleanup but a rewrite of history.
    """
    try:
        feature_store.abandon(feature_id)
    except feature_store.FeatureError as exc:
        return jsonify({"error": str(exc)}), 404
    return jsonify(_ledger_body(feature_id))


@features_bp.route("/api/features/<int:feature_id>/attribute",
                   methods=["POST"])
@require_editor
def api_feature_attribute(feature_id):
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

    row = attribution.attribute(feature_id, trace_id,
                                primary=bool(_payload().get("is_primary")))
    spans.attributed(row)
    return jsonify({"feature_id": row.feature_id, "trace_id": row.trace_id,
                    "provenance": row.provenance,
                    "is_primary": bool(row.is_primary),
                    "round": row.round}), 201


@features_bp.route("/api/features/<int:feature_id>/attribute/<trace_id>",
                   methods=["DELETE"])
@require_editor
def api_feature_unattribute(feature_id, trace_id):
    """Withdraw a declaration.

    A session reaching this feature through a run has no row to remove, and
    says so rather than reporting a silent success: the assertion is the
    task's placement under the feature, and withdrawing it is an act there.
    """
    if attribution.unattribute(feature_id, trace_id):
        spans.unattributed(feature_id,
                           attribution.canonical_trace(trace_id))
        return jsonify({"feature_id": feature_id, "trace_id": trace_id,
                        "unattributed": True})
    reaches = any(row.feature_id == feature_id for row in
                  attribution.attributions_for_session(trace_id))
    detail = (" — it is attributed through a run, which is withdrawn by moving"
              " that run's task, not here" if reaches else "")
    return jsonify({
        "error": f"{trace_id} has no declaration of feature {feature_id}"
                 f"{detail}"}), 404


@features_bp.route("/api/features/<int:feature_id>/refresh", methods=["POST"])
@require_editor
def api_feature_refresh(feature_id):
    """Ask the remote tracker, once, and fold what it says into the log.

    A write despite being read-shaped: it spends someone else's API quota and
    appends to an append-only table. Exactly the `GET …/ledger` versus
    `POST …/refresh` split the task surface drew.

    A failure records the reason on the feature and appends no event, so the
    caller can state that the stage beside it is unchanged — and every other
    figure on the page was local anyway.
    """
    try:
        error = sync_module.refresh(feature_id)
    except sync_module.NotRemote as exc:
        return jsonify({"error": str(exc)}), 400
    except feature_store.FeatureError as exc:
        return jsonify({"error": str(exc)}), 404
    body = _ledger_body(feature_id)
    if error:
        body["error"] = (
            "the tracker could not answer — it may be unreachable, or it may "
            "hold no such issue; the stage shown is unchanged and every other "
            "figure is local")
        return jsonify(body), 502
    return jsonify(body)
