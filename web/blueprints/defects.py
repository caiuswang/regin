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

from flask import Blueprint, Response, jsonify, request

from lib.auth import require_auth, require_editor
from lib.defects import fix_items
from lib.defects import images as defect_images
from lib.defects import query as defect_query
from lib.defects import store
from lib.utils.pagination import clamp_page, clamp_size, list_page
from web.blueprints.image_payload import decoded_image, wanted_idx

defects_bp = Blueprint("defects", __name__)

_FIELDS = {"symptom", "feature_id", "suspect_paths"}

SCOPE_ALL = defect_query.SCOPE_ALL
SCOPE_UNATTRIBUTED = defect_query.SCOPE_UNATTRIBUTED
SCOPE_TRIAGE = defect_query.SCOPE_TRIAGE
SCOPE_FEATURE = defect_query.SCOPE_FEATURE
SCOPE_FIX_ITEM = defect_query.SCOPE_FIX_ITEM

_TRUE = {"1", "true", "yes", "on"}


def _view(row, feature_title, blamed_event_kind, images=None,
          fix_state=None) -> dict:
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
        # The fix task's CURRENT status, and only when asked for: it costs a
        # second read, and a list that always carried it would pay for a
        # column most readers never open. `regin defects list --with-fix-state`
        # is the same read under the same name.
        "fix_state": fix_state,
        # How this defect left the triage queue, or None while it is still in
        # it. Separate from `status` because a declined report and a fixed bug
        # are both closed, and only one of them was ever looked at.
        "triage_state": row.triage_state,
        "triage_reason": row.triage_reason,
        # The row's place on the triage axis as the queue predicate reads it,
        # so a surface never re-derives the snooze-watermark rule.
        "queue_position": store.queue_position(row),
        "opened_at": row.opened_at, "updated_at": row.updated_at,
        "closed_at": row.closed_at,
        # Metadata only — the bytes are a separate GET, so a page of 50
        # reports does not carry a megabyte of base64.
        "images": defect_images.list_for(row.id) if images is None
        else images,
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

    Every scope is a predicate over the same read, not a reader of its own —
    which is what retires the reported bug. `all` came back
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
    # One read for the page's images, not one per row: this is a BLOB table
    # and a per-row call would be an N+1 against it.
    by_defect = defect_images.summaries_for(
        row.defect.id for row in result.items)
    # One read for the whole page's fix tasks, for the same reason — and only
    # when asked, so the default list costs exactly what it did before.
    states = (store.fix_states([row.defect for row in result.items])
              if (request.args.get("with_fix_state") or "").lower() in _TRUE
              else {})
    body["items"] = [_view(row.defect, row.feature_title,
                           row.blamed_event_kind,
                           by_defect.get(row.defect.id, []),
                           states.get(row.defect.id))
                     for row in result.items]
    # The scope and the sort are echoed because the controls above the list
    # have to be able to render what the server actually applied — a sort key
    # the server fell back on, shown as the one that was asked for, is a
    # control that lies about its own list.
    return jsonify(list_page(
        body, population=store.population(query), sort=str(query.sort),
        filters={"scope": query.scope, "status": query.status, "q": query.q,
                 "triage": query.triage_position,
                 "queue": defect_query.QUEUE_OUT if query.queue_out else None,
                 "feature_id": query.feature_id,
                 "fix_item": query.fix_task_id,
                 "project": "none" if query.project_none
                 else query.project_id},
        extras={"scope": query.scope}))


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


def _edit_problem(payload: dict):
    unknown = sorted(set(payload) - {"symptom", "suspect_paths"})
    if unknown:
        return f"unknown field(s): {', '.join(unknown)}"
    if not payload:
        return "send symptom, suspect_paths, or both"
    if "symptom" in payload and (not isinstance(payload["symptom"], str)
                                 or not payload["symptom"].strip()):
        return "symptom is required"
    paths = payload.get("suspect_paths", [])
    if not isinstance(paths, list) or any(not isinstance(p, str) for p in paths):
        return "suspect_paths must be a list of strings"
    return None


@defects_bp.route("/api/defects/<int:defect_id>", methods=["PATCH"])
@require_editor
def api_edit_defect(defect_id: int):
    """Rewrite a defect's wording and/or its suspect paths — open or closed."""
    payload = request.get_json(silent=True)
    payload = payload if isinstance(payload, dict) else {}
    problem = _edit_problem(payload)
    if problem:
        return jsonify({"error": problem}), 400
    return _write(defect_id, lambda: store.edit(
        defect_id, symptom=payload.get("symptom"),
        suspect_paths=payload.get("suspect_paths")))


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
        return _refused(exc)
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
        return _refused(exc)
    return jsonify({"defect": _serialize(row)}), 200


def _refused(exc):
    body = {"error": str(exc)}
    if exc.remedy:
        body["remedy"] = exc.remedy
    return jsonify(body), 409


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


def _fix_item_problem(payload, task_id, create):
    """Why this body cannot be a fix-item write, or None.

    `task_id` and `create` are two different assertions — *that* item is the
    fix, versus write me one from the symptom — so a body naming both has no
    single answer and one naming neither is a no-op the caller would read as
    a write.
    """
    if not isinstance(payload, dict):
        return "the body must name exactly one of task_id or create"
    if create is not True and create is not False:
        return "create is a flag: send true, or leave it out"
    if (task_id is None) == (not create):
        return "name exactly one of task_id or create"
    # `isinstance(True, int)` — and a bool reaching the foreign key would
    # point the defect at task 1.
    if not create and (isinstance(task_id, bool)
                       or not isinstance(task_id, int) or task_id < 1):
        return "task_id must be a positive whole number"
    return None


@defects_bp.route("/api/defects/<int:defect_id>/fix-item", methods=["POST"])
@require_editor
def api_set_fix_item(defect_id: int):
    """Point a defect at the task that will close it, or author that task.

    The pointer is last-writer-wins, so a browser that mis-pointed re-points;
    unpointing it entirely is `DELETE` on the same path.
    """
    payload = request.get_json(silent=True)
    body = payload if isinstance(payload, dict) else {}
    task_id, create = body.get("task_id"), body.get("create", False)
    problem = _fix_item_problem(payload, task_id, create)
    if problem:
        return jsonify({"error": problem}), 400
    if create:
        return _write(defect_id, lambda: fix_items.create_for(defect_id))
    return _write(defect_id, lambda: store.set_fix_item(defect_id, task_id))


@defects_bp.route("/api/defects/<int:defect_id>/fix-item",
                  methods=["DELETE"])
@require_editor
def api_clear_fix_item(defect_id: int):
    """Unpoint a defect from its fix task. The task itself is untouched."""
    return _write(defect_id, lambda: store.clear_fix_item(defect_id))


def _positive_id(value) -> bool:
    # `isinstance(True, int)` — and a bool here would name defect 1.
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _defect_ids(payload) -> "list[int] | None":
    ids = payload.get("defect_ids") if isinstance(payload, dict) else None
    if not isinstance(ids, list) or not ids \
            or not all(_positive_id(i) for i in ids):
        return None
    return ids


@defects_bp.route("/api/defects/fix-task", methods=["POST"])
@require_editor
def api_create_fix_task():
    """One fix task for every defect the body names, or nothing.

    A refusal is `409` with `blocking` listing each defect that cannot join,
    so the page marks them all instead of failing one per retry.
    """
    ids = _defect_ids(request.get_json(silent=True))
    if ids is None:
        return jsonify({"error": "defect_ids must be a non-empty list of "
                                 "defect ids"}), 400
    missing = [i for i in ids if _missing(i)]
    if missing:
        return jsonify({"error": f"no defect {missing[0]}"}), 404
    try:
        task_id, rows = fix_items.create_batch(ids)
    except fix_items.BatchRefused as exc:
        return jsonify({"error": str(exc), "blocking": exc.blocking}), 409
    except store.DefectError as exc:
        return _refused(exc)
    return jsonify({"task_id": task_id,
                    "defects": [_serialize(row) for row in rows]}), 200


@defects_bp.route("/api/defects/<int:defect_id>/merge", methods=["POST"])
@require_editor
def api_merge_defect(defect_id: int):
    """Fold this defect into the one the body names, and keep the other.

    The remedy this module's own docstring has named as a 409 case since it
    was written, with no route that could produce it: `attribute` refuses a
    symptom already reported against that feature and points at `merge`, so an
    operator following that sentence in the browser reached a dead end.

    The keeper is what comes back, because the duplicate no longer exists —
    the store deletes it rather than closing it, so a caller holding the
    duplicate's id must be handed the row that absorbed it.
    """
    payload = request.get_json(silent=True) or {}
    into = payload.get("into")
    if not _positive_id(into):
        return jsonify({"error": "name the defect to keep as `into`"}), 400
    if _missing(into):
        return jsonify({"error": f"no defect {into}"}), 404
    return _write(defect_id, lambda: store.merge(defect_id, into=into))


@defects_bp.route("/api/defects/<int:defect_id>/absorb", methods=["POST"])
@require_editor
def api_absorb_defects(defect_id: int):
    """Fold every defect the body names into this one, in one transaction.

    The keeper is the path's id — the row the operator is looking at when
    they recognise several reports as one bug — so N duplicates are one call
    and one refusal, rather than N merges that can stop halfway.
    """
    payload = request.get_json(silent=True) or {}
    dups = payload.get("duplicates")
    if not isinstance(dups, list) or not dups \
            or not all(_positive_id(d) for d in dups):
        return jsonify({"error": "name the duplicates as `duplicates`, a "
                                 "list of defect ids"}), 400
    gone = [d for d in dups if _missing(d)]
    if gone:
        return jsonify({"error": "no defect "
                        + ", ".join(str(d) for d in gone)}), 404
    return _write(defect_id, lambda: store.merge_many(dups, into=defect_id))


@defects_bp.route("/api/defects/<int:defect_id>/decline", methods=["POST"])
@require_editor
def api_decline_defect(defect_id: int):
    """Route a report out of triage without naming a feature.

    The reason is optional and `missing_check` stays NULL: a report that was
    never a bug has no check that would have caught it, and `close` is the
    route for the ones that do.
    """
    payload = request.get_json(silent=True) or {}
    reason = payload.get("reason")
    if reason is not None and not isinstance(reason, str):
        return jsonify({"error": "reason is text, or leave it out"}), 400
    return _write(defect_id, lambda: store.decline(defect_id, reason=reason))


@defects_bp.route("/api/defects/<int:defect_id>/snooze", methods=["POST"])
@require_editor
def api_snooze_defect(defect_id: int):
    """Hold a defect out of the queue until something writes to it.

    No body: there is nothing to say. The return condition is an event on the
    row, not a date the caller could name — a snooze-until would make the
    queue's contents a function of the clock.
    """
    return _write(defect_id, lambda: store.snooze(defect_id))


@defects_bp.route("/api/defects/<int:defect_id>/unsnooze", methods=["POST"])
@require_editor
def api_unsnooze_defect(defect_id: int):
    """Return a snoozed defect to the queue now, without waiting for a write."""
    return _write(defect_id, lambda: store.unsnooze(defect_id))


@defects_bp.route("/api/defects/<int:defect_id>/restore", methods=["POST"])
@require_editor
def api_restore_defect(defect_id: int):
    """Undo a decline: open again, back in the queue, round unchanged."""
    return _write(defect_id, lambda: store.restore(defect_id))


@defects_bp.route("/api/defects/<int:defect_id>/reopen", methods=["POST"])
@require_editor
def api_reopen_defect(defect_id: int):
    """Record that we claimed this fixed and were wrong. This is the round."""
    return _write(defect_id, lambda: store.reopen(defect_id))


# ── images ───────────────────────────────────────────────────

@defects_bp.route("/api/defects/<int:defect_id>/images", methods=["POST"])
@require_editor
def api_attach_defect_image(defect_id: int):
    """Attach one screenshot to a defect.

    A refusal is a 400 with the reason, not a silent drop: the reporter is
    watching a thumbnail that has to either appear or explain itself.
    """
    if _missing(defect_id):
        return jsonify({"error": f"no defect {defect_id}"}), 404
    try:
        payload = request.get_json(silent=True)
        data = decoded_image(payload)
        return jsonify({"image": defect_images.attach(
            defect_id, data, wanted_idx(payload))}), 201
    except ValueError as exc:
        return jsonify({"error": str(exc), "field": "data_b64"}), 400


@defects_bp.route("/api/defects/<int:defect_id>/images/<int:idx>",
                  methods=["DELETE"])
@require_editor
def api_remove_defect_image(defect_id: int, idx: int):
    """Detach one screenshot. 404 when nothing is at that index."""
    if not defect_images.remove(defect_id, idx):
        return jsonify({"error": "unknown image"}), 404
    return jsonify({"removed": True, "idx": idx})


@defects_bp.route("/api/defects/<int:defect_id>/images/<int:idx>")
@require_auth
def api_get_defect_image(defect_id: int, idx: int):
    """Serve one screenshot as raw bytes, for the thumbnail on the row."""
    found = defect_images.get_bytes(defect_id, idx)
    if found is None:
        return jsonify({"error": "unknown image"}), 404
    data, media_type = found
    # A cached (defect, idx) pair, because the index is stable for as long as
    # the image is. It is NOT stable across a delete of the HIGHEST index —
    # that one is reissued (`ImageSet.remove`), so re-attaching after such a
    # delete can serve the previous picture for the life of the cache. Defect
    # #177.
    return Response(data, mimetype=media_type,
                    headers={"Cache-Control": "private, max-age=86400"})
