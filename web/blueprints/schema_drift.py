"""WebUI surface for PostToolUse payload schema drift.

Lists drift findings, lets editors ratify (auto-extend the JSON schema
on disk) or ignore them. Listing is auth-free; mutations require an
editor session.
"""

from __future__ import annotations

import copy
import difflib
import json
import os
import tempfile
from pathlib import Path

from flask import Blueprint, jsonify, request
from sqlalchemy import text

from lib import audit
from lib.auth import get_current_user, require_editor
from lib.orm import SessionLocal
from lib.providers import build_provider, get_active_provider, is_provider_id
from lib.trace.payload_drift_store import _sha256
from lib.trace.payload_validation import (
    _BASELINE_DIR, _load_schema, _overlay_dir,
    baseline_schema_path, effective_baseline_path, overlay_schema_path,
)


schema_drift_bp = Blueprint('schema_drift', __name__)


# ── Listing ─────────────────────────────────────────────────────────

@schema_drift_bp.route('/api/schema-drift', methods=['GET'])
def api_schema_drift_list():
    """Return drift findings, optionally filtered by status, agent, kind."""
    status_filter = request.args.get('status')  # pending | ratified | ignored | None=all
    agent_filter = request.args.get('agent')
    kind_filter = request.args.get('kind')  # tool | hook_event | None=all
    sql = """
        SELECT id, agent, subject_kind, tool_name, drift_kind, field_path, expected,
               sample_value, sample_payload_sha, claude_version,
               first_seen, last_seen, occurrence_count, status
        FROM payload_schema_drift
    """
    params: dict = {}
    conditions = []
    if status_filter in ('pending', 'ratified', 'ignored'):
        conditions.append("status = :status")
        params['status'] = status_filter
    if agent_filter:
        conditions.append("agent = :agent")
        params['agent'] = agent_filter
    if kind_filter in ('tool', 'hook_event'):
        conditions.append("subject_kind = :kind")
        params['kind'] = kind_filter
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY agent, tool_name, drift_kind, field_path"
    with SessionLocal() as session:
        rows = [dict(r._mapping) for r in session.execute(text(sql), params)]
    by_agent: dict[str, dict[str, list[dict]]] = {}
    for r in rows:
        by_agent.setdefault(r['agent'], {}).setdefault(r['tool_name'], []).append(r)
    return jsonify({
        'rows': rows,
        'by_agent': by_agent,
        'agents': sorted(by_agent.keys()),
        'total': len(rows),
    })


@schema_drift_bp.route('/api/schema-drift/schemas', methods=['GET'])
def api_schema_drift_schemas():
    """List every known schema (one row per agent+tool) plus its state:
    has baseline, has overlay, drift counts by status. The page treats
    schemas as the primary entity; drift rows are the detail view per
    schema."""
    agent_filter = request.args.get('agent')
    kind_filter = request.args.get('kind')  # tool | hook_event | None=all
    agents = _discover_agents(agent_filter)
    counts = _drift_counts_by_schema()
    rows: list[dict] = []
    for agent in agents:
        if kind_filter in (None, 'tool'):
            for tool in _known_tools():
                row = _schema_row(agent, tool, 'tool', counts)
                if row:
                    rows.append(row)
        if kind_filter in (None, 'hook_event'):
            for event in _known_hook_events():
                row = _schema_row(agent, event, 'hook_event', counts)
                if row:
                    rows.append(row)
    kpi = _summary_kpis(rows)
    return jsonify({'rows': rows, 'kpi': kpi, 'agents': sorted(agents)})


def _schema_row(agent: str, name: str, subject_kind: str, counts: dict) -> dict | None:
    """Build one /schemas row for (agent, name, subject_kind), or None when
    neither a baseline nor an overlay file exists for it."""
    baseline = effective_baseline_path(agent, name, subject_kind).is_file()
    overlay = overlay_schema_path(agent, name, subject_kind).is_file()
    if not baseline and not overlay:
        return None
    stats = counts.get((agent, subject_kind, name), {})
    return {
        'agent': agent,
        'subject_kind': subject_kind,
        'tool': name,
        'baseline': baseline,
        'overlay': overlay,
        'pending': stats.get('pending', 0),
        'ratified': stats.get('ratified', 0),
        'ignored': stats.get('ignored', 0),
        'last_drift_seen': stats.get('last_seen'),
        'state': _schema_state(stats, overlay),
    }


def _discover_agents(agent_filter: str | None) -> list[str]:
    found = {'claude'}  # always show, even if no rows yet
    if agent_filter:
        return [agent_filter]
    with SessionLocal() as session:
        for row in session.execute(text(
            "SELECT DISTINCT agent FROM payload_schema_drift",
        )):
            found.add(row[0])
    return sorted(found)


def _known_tools() -> list[str]:
    from hook_manager.handlers.post_tool_trace import _TOOL_BUILDERS
    return sorted(set(_TOOL_BUILDERS.keys()) | {'_mcp_wildcard'})


def _known_hook_events() -> list[str]:
    """Hook event names with a known schema: `_hooks/*.schema.json` stems
    (across every agent dir) unioned with the distinct hook_event names
    that have already produced drift rows."""
    found: set[str] = set()
    for base in (_BASELINE_DIR, _overlay_dir()):
        for hooks_dir in base.glob('*/_hooks'):
            for path in hooks_dir.glob('*.schema.json'):
                found.add(path.name.removesuffix('.schema.json'))
    with SessionLocal() as session:
        for row in session.execute(text(
            "SELECT DISTINCT tool_name FROM payload_schema_drift "
            "WHERE subject_kind = 'hook_event'",
        )):
            found.add(row[0])
    return sorted(found)


def _drift_counts_by_schema() -> dict[tuple[str, str, str], dict]:
    out: dict[tuple[str, str, str], dict] = {}
    with SessionLocal() as session:
        for row in session.execute(text("""
            SELECT agent, subject_kind, tool_name, status,
                   COUNT(*) AS n, MAX(last_seen) AS last_seen
            FROM payload_schema_drift
            GROUP BY agent, subject_kind, tool_name, status
        """)):
            key = (row.agent, row.subject_kind, row.tool_name)
            entry = out.setdefault(key, {})
            entry[row.status] = row.n
            if 'last_seen' not in entry or (row.last_seen or '') > (entry['last_seen'] or ''):
                entry['last_seen'] = row.last_seen
    return out


def _schema_state(stats: dict, overlay: bool) -> str:
    if stats.get('pending'):
        return 'drift'
    if overlay or stats.get('ratified'):
        return 'overlaid'
    return 'clean'


def _summary_kpis(rows: list[dict]) -> dict:
    return {
        'total': len(rows),
        'clean': sum(1 for r in rows if r['state'] == 'clean'),
        'drift': sum(1 for r in rows if r['state'] == 'drift'),
        'overlaid': sum(1 for r in rows if r['state'] == 'overlaid'),
        'pending_findings': sum(r['pending'] for r in rows),
    }


@schema_drift_bp.route('/api/schema-drift/schema', methods=['GET'])
def api_schema_drift_schema():
    """Return the merged schema (baseline + overlay) for one (agent, tool)
    plus the paths, so the WebUI can show clean schemas — not just ones
    that have produced drift findings."""
    agent = request.args.get('agent', 'claude')
    tool = request.args.get('tool', '')
    kind = request.args.get('kind', 'tool')
    if not tool:
        return jsonify({'error': 'tool query param required'}), 400
    schema = _load_schema(agent, tool, kind)
    if schema is None:
        return jsonify({'error': 'no schema for that (agent, tool)'}), 404
    return jsonify({
        'agent': agent,
        'tool': tool,
        'subject_kind': kind,
        'schema': schema,
        'baseline_path': str(baseline_schema_path(agent, tool, kind)),
        'overlay_path': str(overlay_schema_path(agent, tool, kind)),
        'overlay_exists': overlay_schema_path(agent, tool, kind).is_file(),
    })


@schema_drift_bp.route('/api/schema-drift/schema/diff', methods=['GET'])
def api_schema_drift_schema_diff():
    """Preview the schema after applying every pending unknown_field
    drift for a given (agent, tool). Renders as a unified diff so the
    user can see, like git diff, what their overlay would gain if they
    ratified everything in the queue."""
    agent = request.args.get('agent', 'claude')
    tool = request.args.get('tool', '')
    kind = request.args.get('kind', 'tool')
    if not tool:
        return jsonify({'error': 'tool query param required'}), 400
    current = _load_schema(agent, tool, kind)
    if current is None:
        return jsonify({'error': 'no schema for that (agent, tool)'}), 404

    drifts = _pending_unknown_field_drifts(agent, tool)
    proposed = _apply_drifts_to_schema(current, drifts)
    return jsonify({
        'agent': agent,
        'tool': tool,
        'subject_kind': kind,
        'pending_count': len(drifts),
        'current': current,
        'proposed': proposed,
        'unified_diff': _unified_diff(current, proposed),
    })


def _pending_unknown_field_drifts(agent: str, tool: str) -> list[dict]:
    with SessionLocal() as session:
        rows = session.execute(text("""
            SELECT id, agent, tool_name, drift_kind, field_path,
                   sample_value, claude_version
            FROM payload_schema_drift
            WHERE agent = :agent AND tool_name = :tool
              AND status = 'pending' AND drift_kind = 'unknown_field'
            ORDER BY field_path
        """), {'agent': agent, 'tool': tool}).mappings().all()
    return [dict(r) for r in rows]


def _apply_drifts_to_schema(current: dict, drifts: list[dict]) -> dict:
    """Return a new schema dict with every unknown_field drift applied.
    Mirrors `_apply_ratify_to_schema`'s field-insertion logic but is
    pure (no I/O, no cache touch) so the diff endpoint can preview."""
    proposed = copy.deepcopy(current)
    for drift in drifts:
        parts = _split_field_path(drift['field_path'])
        if not parts:
            continue
        parent = _navigate_to_parent(proposed, parts)
        parent_props = parent.setdefault('properties', {})
        leaf = parts[-1].split('[')[0]
        if leaf and leaf not in parent_props:
            parent_props[leaf] = {'type': _inferred_type(drift['sample_value'])}
    return proposed


def _unified_diff(current: dict, proposed: dict) -> str:
    a = json.dumps(current, indent=2, sort_keys=True).splitlines(keepends=True)
    b = json.dumps(proposed, indent=2, sort_keys=True).splitlines(keepends=True)
    return ''.join(difflib.unified_diff(
        a, b, fromfile='current.schema.json', tofile='proposed.schema.json',
        n=3,
    ))


@schema_drift_bp.route('/api/schema-drift/<int:drift_id>/detail', methods=['GET'])
def api_schema_drift_detail(drift_id: int):
    """Everything needed to investigate one finding: the merged current
    schema for that (agent, tool), the raw payload that triggered it
    (looked up in ~/.claude/hook-payloads.jsonl by sample_payload_sha),
    and — for unknown_field — what the schema would gain on ratify."""
    drift = _load_full_drift_row(drift_id)
    if drift is None:
        return jsonify({'error': 'not found'}), 404
    agent = drift.get('agent') or 'claude'
    tool = drift['tool_name']
    kind = drift.get('subject_kind') or request.args.get('kind', 'tool')
    return jsonify({
        'drift': drift,
        'schema': _load_schema(agent, tool, kind),
        'baseline_path': str(baseline_schema_path(agent, tool, kind)),
        'overlay_path': str(overlay_schema_path(agent, tool, kind)),
        'overlay_exists': overlay_schema_path(agent, tool, kind).is_file(),
        'payload': _lookup_payload(drift.get('sample_payload_sha'), agent),
        'proposed_change': _propose_change(drift),
    })


@schema_drift_bp.route('/api/schema-drift/summary', methods=['GET'])
def api_schema_drift_summary():
    """Lightweight pending count for the nav badge."""
    with SessionLocal() as session:
        pending = session.execute(text(
            "SELECT COUNT(*) FROM payload_schema_drift WHERE status = 'pending'"
        )).scalar() or 0
    return jsonify({'pending': pending})


# ── Mutations ───────────────────────────────────────────────────────

_DRIFT_COLUMNS_FULL = (
    "id, agent, subject_kind, tool_name, drift_kind, field_path, expected, "
    "sample_value, sample_payload_sha, claude_version, first_seen, last_seen, "
    "occurrence_count, status"
)


def _load_drift_row(drift_id: int) -> dict | None:
    with SessionLocal() as session:
        row = session.execute(text(
            "SELECT id, agent, subject_kind, tool_name, drift_kind, field_path, "
            "sample_value, claude_version, status FROM payload_schema_drift "
            "WHERE id = :id"
        ), {'id': drift_id}).mappings().first()
    return dict(row) if row else None


def _load_full_drift_row(drift_id: int) -> dict | None:
    with SessionLocal() as session:
        row = session.execute(text(
            f"SELECT {_DRIFT_COLUMNS_FULL} FROM payload_schema_drift WHERE id = :id"
        ), {'id': drift_id}).mappings().first()
    return dict(row) if row else None


def _payload_log_path_for(agent: str | None) -> Path:
    """Resolve the hook-payloads.jsonl path for a drift row's agent.

    Each provider writes its payloads to its own log (Claude →
    ~/.claude, Kimi → ~/.kimi-code, …), so a kimi finding must be looked
    up in kimi's log even when Claude is the active provider. Falls back
    to the active provider for an unknown/missing agent."""
    provider = build_provider(agent) if is_provider_id(agent) else get_active_provider()
    return Path(str(provider.hook_payload_log_path()))


def _lookup_payload(target_sha: str | None, agent: str | None = None) -> dict | None:
    """Find the raw payload in the agent's hook-payloads.jsonl by sha256
    of the line. Returns the parsed entry's `payload` dict, or None if
    the log rotated past it / hash missing."""
    if not target_sha:
        return None
    path = _payload_log_path_for(agent)
    if not path.is_file():
        return None
    try:
        with path.open() as fh:
            for line in fh:
                entry = json.loads(line)
                payload = entry.get('payload')
                if payload is None:
                    continue
                if _sha256(payload) == target_sha:
                    return payload
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    return None


def _propose_change(drift: dict) -> dict | None:
    """For unknown_field, describe what ratify would add. Returns None
    for other kinds (no ratify support yet)."""
    if drift['drift_kind'] != 'unknown_field':
        return None
    parts = _split_field_path(drift['field_path'])
    if not parts:
        return None
    leaf = parts[-1].split('[')[0]
    return {
        'kind': 'add_property',
        'path': drift['field_path'],
        'leaf': leaf,
        'schema_to_insert': {'type': _inferred_type(drift['sample_value'])},
    }


def _set_status(drift_id: int, status: str) -> None:
    with SessionLocal() as session:
        session.execute(text(
            "UPDATE payload_schema_drift SET status = :status WHERE id = :id"
        ), {'id': drift_id, 'status': status})
        session.commit()


def _audit(action: str, drift: dict, extra: dict | None = None) -> None:
    user = get_current_user()
    payload = {
        'drift_id': drift['id'],
        'agent': drift.get('agent'),
        'tool': drift['tool_name'],
        'kind': drift['drift_kind'],
        'field_path': drift['field_path'],
    }
    if extra:
        payload.update(extra)
    audit.log_action(
        user['id'] if user else None,
        user['username'] if user else 'anon',
        action,
        f"schema_drift:{drift['id']}",
        payload,
    )


@schema_drift_bp.route('/api/schema-drift/<int:drift_id>/ignore', methods=['POST'])
@require_editor
def api_schema_drift_ignore(drift_id: int):
    drift = _load_drift_row(drift_id)
    if drift is None:
        return jsonify({'ok': False, 'error': 'not found'}), 404
    _set_status(drift_id, 'ignored')
    _audit('schema_drift_ignore', drift)
    return jsonify({'ok': True})


@schema_drift_bp.route('/api/schema-drift/<int:drift_id>', methods=['DELETE'])
@require_editor
def api_schema_drift_delete(drift_id: int):
    drift = _load_drift_row(drift_id)
    if drift is None:
        return jsonify({'ok': False, 'error': 'not found'}), 404
    with SessionLocal() as session:
        session.execute(text(
            "DELETE FROM payload_schema_drift WHERE id = :id"
        ), {'id': drift_id})
        session.commit()
    _audit('schema_drift_delete', drift)
    return jsonify({'ok': True})


@schema_drift_bp.route('/api/schema-drift/<int:drift_id>/ratify', methods=['POST'])
@require_editor
def api_schema_drift_ratify(drift_id: int):
    """Auto-extend <Tool>.schema.json to include the new field.

    Only `unknown_field` findings are ratifiable in v1. Other drift
    kinds (missing_required, type_mismatch, enum_violation, unknown_tool)
    are read-only here — the schema needs a human edit. Use Ignore or
    Delete instead.
    """
    drift = _load_drift_row(drift_id)
    if drift is None:
        return jsonify({'ok': False, 'error': 'not found'}), 404
    if drift['drift_kind'] != 'unknown_field':
        return jsonify({
            'ok': False,
            'error': f"ratify only supports unknown_field; this is {drift['drift_kind']}",
        }), 400
    try:
        _apply_ratify_to_schema(drift)
    except FileNotFoundError as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 404
    _set_status(drift_id, 'ratified')
    _audit('schema_drift_ratify', drift, {'schema_file': f"{drift['tool_name']}.schema.json"})
    return jsonify({'ok': True})


# ── Schema mutation helpers ─────────────────────────────────────────


def _atomic_write_json(path: Path, data: dict) -> None:
    """Write JSON atomically so concurrent readers never see a half file."""
    parent = path.parent
    fd, tmp_name = tempfile.mkstemp(prefix='.tmp-', suffix='.json', dir=parent)
    try:
        with os.fdopen(fd, 'w') as fh:
            json.dump(data, fh, indent=2)
            fh.write('\n')
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _inferred_type(sample_value: str) -> str:
    """Infer a JSON Schema type from the truncated repr stored on the row.

    `sample_value` is `_sample_repr(value)` — best-effort JSON. Fall
    back to 'string' if we can't decode it (worst case: schema gets a
    too-loose type that a future drift can tighten).
    """
    try:
        parsed = json.loads(sample_value.rstrip('…'))
    except (ValueError, TypeError):
        return 'string'
    if isinstance(parsed, bool):
        return 'boolean'
    if isinstance(parsed, int):
        return 'integer'
    if isinstance(parsed, float):
        return 'number'
    if isinstance(parsed, list):
        return 'array'
    if isinstance(parsed, dict):
        return 'object'
    if parsed is None:
        return 'null'
    return 'string'


def _navigate_to_parent(schema: dict, parts: list[str]) -> dict:
    """Walk `schema` along `parts[:-1]`, creating `properties` containers
    when missing. Array indices ('[0]') step into the array's `items`
    schema. Returns the dict where the new property should be added."""
    node = schema
    for part in parts[:-1]:
        if part.endswith(']') and '[' in part:
            base = part[: part.index('[')]
            if base:
                node = _descend_property(node, base)
            node = node.setdefault('items', {'type': 'object', 'additionalProperties': True})
            if 'properties' not in node:
                node['properties'] = {}
            continue
        node = _descend_property(node, part)
    if 'properties' not in node:
        node['properties'] = {}
    return node


def _descend_property(node: dict, name: str) -> dict:
    props = node.setdefault('properties', {})
    if name not in props:
        props[name] = {'type': 'object', 'additionalProperties': True}
    return props[name]


def _overlay_title(tool_name: str, subject_kind: str) -> str:
    if subject_kind == 'hook_event':
        return f'{tool_name} hook payload (user overlay)'
    return f'{tool_name} PostToolUse payload (user overlay)'


def _load_or_seed_overlay(
    agent: str, tool_name: str, subject_kind: str = 'tool',
) -> tuple[Path, dict]:
    """Return (overlay_path, overlay_dict).

    If the overlay doesn't exist yet, seed it as a minimal stub —
    enough scaffolding for the navigation+write helpers to find their
    way to the right nested `properties` dict. The validator then
    deep-merges the overlay onto the repo baseline, so the stub never
    erases anything the baseline already declares. Hook overlays land
    under `_hooks/` and carry a hook-flavored title."""
    path = overlay_schema_path(agent, tool_name, subject_kind)
    if path.is_file():
        return path, json.loads(path.read_text())
    # Seed from the lineage-resolved baseline so an inheriting provider
    # (e.g. kimi → claude) can still ratify onto its own overlay.
    baseline = effective_baseline_path(agent, tool_name, subject_kind)
    if not baseline.is_file():
        raise FileNotFoundError(f"no baseline schema for {agent}/{tool_name}")
    return path, {
        '$schema': 'https://json-schema.org/draft/2020-12/schema',
        'title': _overlay_title(tool_name, subject_kind),
        'type': 'object',
        'additionalProperties': True,
        'properties': {},
    }


def _apply_ratify_to_schema(drift: dict) -> None:
    agent = drift.get('agent') or 'claude'
    subject_kind = drift.get('subject_kind') or 'tool'
    path, overlay = _load_or_seed_overlay(agent, drift['tool_name'], subject_kind)

    parts = _split_field_path(drift['field_path'])
    if not parts:
        raise ValueError(f"empty field_path on drift {drift['id']}")

    parent_props = _navigate_to_parent(overlay, parts).setdefault('properties', {})
    leaf = parts[-1].split('[')[0]  # strip any trailing array index
    if leaf and leaf not in parent_props:
        parent_props[leaf] = {'type': _inferred_type(drift['sample_value'])}

    if agent == 'claude':
        # x-claude-versions is Claude-specific; other agents would have
        # their own x-<agent>-versions list once wired.
        versions = overlay.setdefault('x-claude-versions', [])
        ver = drift.get('claude_version')
        if ver and ver not in versions:
            versions.append(ver)

    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(path, overlay)
    _load_schema.cache_clear()  # otherwise next validate() reads the stale copy


def _split_field_path(path: str) -> list[str]:
    """Split 'tool_input.questions[0].header' into ['tool_input',
    'questions[0]', 'header']. Array indices stay attached to their
    parent name."""
    return [p for p in path.split('.') if p]
