"""Session detail, subtree and span-children endpoints."""

from __future__ import annotations

import hashlib
import json

from flask import request, jsonify
from lib.trace import trace_service
from web.blueprints.trace import trace_bp
from web.blueprints.trace.sessions.phase import _session_summary


def _wire_nodes(nodes, *, deep):
    """Project tree nodes for the wire: structure only, spans named by id.

    Every response that carries a tree also carries the span bodies in its
    `spans` list, so a node embedding its own `data` copy shipped each span
    twice — and a group node, whose `data.spans` inlines its children's
    bodies, shipped some of them a third time. One prompt's `?deep=1` subtree
    came to 841KB, of which 491KB was the second and third copies. The client
    rejoins the two halves on arrival (`hydrateNodes` in useTraceData).

    What survives is what belongs to the NODE rather than to its span: the
    expander flags, the child count, and a group's rolled-up envelope, which
    spans the whole subtree and so differs from the group span's own times.

    `deep=False` is the lazy-expand shape — direct children only, each marked
    `leaf` so the frontend knows whether to offer an expander.
    """
    out = []
    for node in nodes:
        data = node['data']
        children = node.get('children') or []
        wire = {'key': node['key'], 'span_id': data['span_id']}
        if data.get('isGroup'):
            wire['isGroup'] = True
            wire['start_time'] = data.get('start_time')
            wire['end_time'] = data.get('end_time')
        if data.get('isSpan'):
            wire['isSpan'] = True
        if deep:
            if children:
                wire['children'] = _wire_nodes(children, deep=True)
        else:
            wire['leaf'] = not children
            if children:
                inline = data.get('spans')
                wire['child_count'] = len(inline) if inline else len(children)
        out.append(wire)
    return out


def _shallow_nodes(nodes):
    """Direct-children wire nodes (no nesting) — see `_wire_nodes`."""
    return _wire_nodes(nodes, deep=False)


@trace_bp.route('/api/sessions/<trace_id>')
def api_session_detail(trace_id):
    """Read-only: projects spans into a tree in memory. Does not mutate the DB.

    Orphan-grafting and parent-envelope widening happen as a pure projection
    on every call. Callers that want the cleanup persisted (e.g. for analytics
    queries) should POST to /api/sessions/<trace_id>/materialize.

    Pass `?shallow=1` to return only the first-class (root) spans; children
    are fetched on demand via /api/sessions/<trace_id>/spans/<span_id>/children.
    """
    shallow = request.args.get('shallow', '').lower() in ('1', 'true')
    widened, tree = trace_service.fetch_session_projection(
        trace_id, drop_delegated=True)
    summary = _session_summary(trace_id)
    if shallow:
        root_ids = {n['data']['span_id'] for n in tree}
        return jsonify({
            'trace_id': trace_id,
            'spans': [s for s in widened if s['span_id'] in root_ids],
            'tree': _shallow_nodes(tree),
            'span_count_total': len(widened),
            **summary,
        })
    return jsonify({
        'trace_id': trace_id,
        'spans': widened,
        'tree': tree,
        'span_count_total': len(widened),
        **summary,
    })


@trace_bp.route('/api/sessions/<trace_id>/subtrees')
def api_session_subtrees(trace_id):
    """Deep subtrees for several roots in ONE projection.

    The trace view refreshes the trailing roots on every reconcile: children
    are still streaming under the active prompt, and a background agent can
    land spans under an older one. Asked one root at a time, that ran
    `fetch_session_projection` — a whole-session merge — once per root, three
    times over, for one screen's worth of new spans.

    Span bodies are deduplicated across the requested roots, which matters
    because a subagent's spans can hang under more than one of them.
    """
    ids = _csv_arg('ids')
    if not ids:
        return jsonify({'error': 'ids is required'}), 400
    known = _parse_known_watermarks(request.args.get('known'))
    dirty = set(_csv_arg('dirty'))
    seen = _parse_seen_digests(request.args.get('seen'))
    widened, tree = trace_service.fetch_session_projection(
        trace_id, drop_delegated=True)
    by_id = {s['span_id']: s for s in widened}

    subtrees, wanted, unchanged, digests = _collect_subtrees(
        tree, ids, by_id, known, dirty, seen)
    return jsonify({
        'trace_id': trace_id,
        'subtrees': subtrees,
        # Named rather than merely omitted: absent from `subtrees` already
        # means "this root has no children", which the client acts on by
        # clearing them. Silence could not carry both answers.
        'unchanged': unchanged,
        'digests': digests,
        'spans': _known_spans(by_id, wanted),
    })


def _csv_arg(name) -> list:
    return [i for i in (request.args.get(name) or '').split(',') if i]


def _known_spans(by_id, wanted) -> list:
    return [by_id[i] for i in wanted if i in by_id]


def _parse_seen_digests(raw) -> dict:
    """`{root_span_id: structure digest the client last applied}`."""
    out: dict = {}
    for pair in (raw or '').split(','):
        root, _, value = pair.partition(':')
        if root and value:
            out[root] = value
    return out


def _collect_subtrees(tree, ids, by_id, known, dirty, seen=None):
    """`({root: wire nodes}, {span ids whose body must ship}, [unchanged roots])`.

    A root absent from the projection is left out of the map entirely; the
    client reads that as "no children" and clears whatever it was holding,
    which is how a retired or reparented root stops lingering in the timeline.

    A root whose structure hashes to what the caller says it already holds, and
    for which no body needs sending, is reported as unchanged instead of being
    serialised again. The refresh asks about the trailing few prompts on every
    reconcile, and the older ones have almost always finished — one of them ran
    to 200 nodes and 16KB of structure, re-sent every few seconds to describe a
    subtree that had not moved in minutes.
    """
    seen = seen or {}
    subtrees: dict = {}
    digests: dict = {}
    wanted: set[str] = set()
    unchanged: list = []
    for span_id in ids:
        children = _find_child_nodes(tree, span_id)
        if children is None:
            continue
        nodes = _wire_nodes(children, deep=True)
        descendants: set[str] = set()
        _collect_descendant_ids(children, descendants)
        watermark = known.get(span_id)
        bodies = {d for d in descendants
                  if _body_needed(by_id.get(d), watermark, dirty)}
        digest = _structure_digest(nodes)
        digests[span_id] = digest
        # Only when there is also nothing to deliver: a subtree can keep its
        # shape while one of its spans grows.
        if not bodies and seen.get(span_id) == digest:
            unchanged.append(span_id)
            continue
        subtrees[span_id] = nodes
        wanted.update(bodies)
    return subtrees, wanted, unchanged, digests


def _structure_digest(nodes) -> str:
    """Stable hash of one subtree's wire shape."""
    payload = json.dumps(nodes, sort_keys=True, separators=(',', ':'))
    return hashlib.sha1(payload.encode()).hexdigest()[:16]


def _parse_known_watermarks(raw) -> dict:
    """`{root_span_id: highest descendant row id the client already holds}`.

    Wire form is `root:id,root:id`. A root the client has never deep-loaded is
    simply absent, which is what makes it receive every body.
    """
    out: dict = {}
    for pair in (raw or '').split(','):
        root, _, value = pair.partition(':')
        if not root or not value.isdigit():
            continue
        out[root] = int(value)
    return out


def _body_needed(span, watermark, dirty: set) -> bool:
    """Does the client need this span's body, or does it already hold it?

    Withheld only when all three hold, so every uncertainty ships the body:

    * the client deep-loaded this subtree past this span's row id — a span it
      has never seen is always above the watermark, because a new span is an
      INSERT and takes a fresh id (an in-place rewrite keeps its id, which is
      why the watermark alone is not the whole answer);
    * the push stream did not name it as changed since;
    * the span has settled. An unfinished span is still being written, and its
      body is the one thing that grows without any identifier moving;
    * the body was not DERIVED from a row the client has yet to read. A
      serve-time join builds one span's body out of another's, so the enriched
      span settles and keeps its id while its body still changes —
      `DERIVED_FROM_ID_KEY` names the row it came from, and the same watermark
      test applied to that id is what makes the refresh both arrive and stop.
    """
    if span is None or watermark is None:
        return True
    if span.get('span_id') in dirty:
        return True
    row_id = span.get('id')
    if row_id is None or row_id > watermark:
        return True
    if _derived_from_id(span, watermark):
        return True
    return not (span.get('end_time') and span.get('status_code') != 'PENDING')


def _derived_from_id(span, watermark) -> bool:
    """Was this span's body built from a row above the client's watermark?"""
    from lib.trace.merge import DERIVED_FROM_ID_KEY
    attrs = span.get('attributes')
    source = attrs.get(DERIVED_FROM_ID_KEY) if isinstance(attrs, dict) else None
    return isinstance(source, int) and source > watermark


def _find_child_nodes(nodes, span_id):
    """The child nodes of `span_id` in a projected tree, or None if absent."""
    for node in nodes:
        if node['data']['span_id'] == span_id:
            return node.get('children', [])
        found = _find_child_nodes(node.get('children', []), span_id)
        if found is not None:
            return found
    return None


def _collect_descendant_ids(nodes, out: set) -> None:
    for node in nodes:
        out.add(node['data']['span_id'])
        _collect_descendant_ids(node.get('children', []), out)


@trace_bp.route('/api/sessions/<trace_id>/spans/<span_id>/children')
def api_session_span_children(trace_id, span_id):
    """Return the children of `span_id`.

    By default (lazy-expand flow) returns direct children only as shallow
    tree nodes. Pass `?deep=1` to get the full subtree with inline
    children — used by the Trace view's reload path to refresh the
    active prompt's subtree without re-fetching the whole session.
    """
    deep = request.args.get('deep', '').lower() in ('1', 'true')
    widened, tree = trace_service.fetch_session_projection(
        trace_id, drop_delegated=True)
    by_id = {s['span_id']: s for s in widened}

    children = _find_child_nodes(tree, span_id) or []
    if deep:
        descendant_ids: set[str] = set()
        _collect_descendant_ids(children, descendant_ids)
        return jsonify({
            'trace_id': trace_id,
            'parent_span_id': span_id,
            'children': _wire_nodes(children, deep=True),
            'spans': [s for i, s in by_id.items() if i in descendant_ids],
        })
    child_ids = {n['data']['span_id'] for n in children}
    return jsonify({
        'trace_id': trace_id,
        'parent_span_id': span_id,
        'children': _shallow_nodes(children),
        'spans': [s for i, s in by_id.items() if i in child_ids],
    })
