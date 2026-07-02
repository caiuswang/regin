"""Validate PostToolUse payloads against committed JSON schemas.

Drift findings flow into `payload_schema_drift` and surface in the
WebUI for manual review. The hot path runs from `trace_payload.py`
after the catch-all JSONL append, so validation must never raise.

Design notes:
  * Schemas use snake_case property names. Top-level keys of
    `tool_input` / `tool_response` are normalized by
    `hook_manager.core._normalize_payload`, but **nested** keys (e.g.
    `tool_response.file.numLines`) are not. The unknown-field walker
    therefore dedupes camelCase aliases via `_to_snake` before flagging.
  * `additionalProperties: true` is set on every committed schema so
    jsonschema itself doesn't reject unknown fields — the recursive
    walker reports them as `unknown_field` findings instead. This
    keeps jsonschema's role focused on type / required / enum drift.
"""

from __future__ import annotations

import functools
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator

from hook_manager.core import _to_snake


_BASELINE_DIR = Path(__file__).parent / "payload_schemas"
_MCP_PREFIX = "mcp__"
_MCP_WILDCARD = "_mcp_wildcard"
_SAMPLE_REPR_MAX = 200
_DEFAULT_AGENT = "claude"


def _overlay_dir() -> Path:
    """Per-user overlay root. Resolved lazily so test-time settings
    overrides take effect without an import-time freeze."""
    from lib.settings import settings
    return Path(settings.payload_schemas_overlay_dir)

# Envelope fields are present on every PostToolUse payload regardless of
# tool. We don't want to enumerate them in every per-tool schema, and we
# don't want to report them as drift, so the walker treats them as always-
# known at the top level.
_ENVELOPE_KEYS: frozenset[str] = frozenset({
    'session_id', 'transcript_path', 'cwd', 'permission_mode',
    'hook_event_name', 'tool_use_id', 'agent_id', 'agent_type',
    'permission_decision',
    # Tool-independent metadata Claude Code stamps on every PostToolUse
    # payload: `duration_ms` (tool execution time, captured onto tool
    # spans) and `effort` ({level: …}, captured per-turn on turn_usage).
    'duration_ms', 'effort',
    # `prompt_id` (Claude Code 2.1.195+) is a per-submission UUID stamped on
    # EVERY hook/tool payload, correlating it to the originating user prompt.
    # It is universal envelope metadata — captured onto tool spans as
    # `source_prompt_id` (post_tool_trace) — so it must never flag as drift.
    'prompt_id',
})

# Always-present top-level keys on every hook-event payload, regardless of
# event. The hook analog of `_ENVELOPE_KEYS` for the `subject_kind='hook_event'`
# axis; selected by `_envelope_keys` at the top level of the walker. Kept
# deliberately distinct (and narrow) from `_ENVELOPE_KEYS`: the tool envelope
# carries tool-flavored keys (permission_mode, tool_use_id, agent_id, …) that
# are NOT universal across hook events, so reusing it for hook payloads would
# mass-false-positive. Everything beyond these four belongs in the per-event
# schema, not the envelope.
_HOOK_COMMON_KEYS: frozenset[str] = frozenset({
    'session_id', 'transcript_path', 'cwd', 'hook_event_name',
    # See `_ENVELOPE_KEYS`: `prompt_id` (2.1.195+) rides every hook-event
    # payload too, so it is common envelope metadata on this axis as well.
    'prompt_id',
})


def _envelope_keys(subject_kind: str) -> frozenset[str]:
    """Always-known top-level keys for the given subject_kind axis."""
    return _HOOK_COMMON_KEYS if subject_kind == 'hook_event' else _ENVELOPE_KEYS


# Always-known keys directly under `tool_response`. `output` is regin's
# canonical single-result-blob key: providers that return one undifferentiated
# result blob (e.g. Kimi's `{output, isError}` envelope) land it here, and
# `_apply_tool_field_aliases` wraps a bare-string `tool_output` as
# `{'output': …}` too. Treating it as known means a tool that inherits another
# provider's per-tool schema (Kimi → Claude) doesn't flag the blob as drift,
# without enumerating `output` in every per-tool `tool_response`.
_RESULT_ENVELOPE_KEYS: frozenset[str] = frozenset({'output'})


@dataclass(frozen=True)
class DriftFinding:
    """One observed schema drift on a payload."""

    agent: str               # provider id (claude, codex, …)
    tool_name: str
    drift_kind: str          # missing_required | type_mismatch | unknown_field | enum_violation | unknown_tool
    field_path: str          # e.g. 'tool_input.questions[0].header'
    expected: str | None     # JSON Schema type/const if applicable
    actual_sample: str       # truncated repr of the offending value
    subject_kind: str = 'tool'  # 'tool' | 'hook_event'


def _schema_filename(tool_name: str) -> str:
    if tool_name.startswith(_MCP_PREFIX):
        return f"{_MCP_WILDCARD}.schema.json"
    return f"{tool_name}.schema.json"


def _schema_relpath(tool_name: str, subject_kind: str) -> Path:
    """Schema path relative to an agent dir. Hook events live under
    `_hooks/<name>.schema.json` (no mcp-wildcard handling); tools keep the
    flat `<tool>.schema.json` filename incl. mcp-wildcard collapsing."""
    if subject_kind == 'hook_event':
        return Path('_hooks') / f'{tool_name}.schema.json'
    return Path(_schema_filename(tool_name))


def baseline_schema_path(agent: str, tool_name: str, subject_kind: str = 'tool') -> Path:
    """Repo-tracked baseline schema path (`lib/trace/payload_schemas/<agent>/`)."""
    return _BASELINE_DIR / agent / _schema_relpath(tool_name, subject_kind)


# Schema lineage: a provider whose tool/hook payloads are 1:1 with a parent
# provider reuses the parent's committed schemas until it ships its own. Kimi
# Code mirrors Claude Code's hook+tool payload surface (event names and the
# common fields line up 1:1 — see `lib/providers/kimi`), so kimi inherits
# claude's schemas instead of flagging every call as `unknown_tool`.
_SCHEMA_PARENT: dict[str, str] = {'kimi': 'claude'}


def effective_baseline_path(
    agent: str, tool_name: str, subject_kind: str = 'tool',
) -> Path:
    """Baseline path resolved through schema lineage: the agent's own
    baseline if it exists, else its parent's (`_SCHEMA_PARENT`). Falls back
    to the agent's own (possibly missing) path when neither exists, so
    callers can still detect 'no baseline'."""
    own = baseline_schema_path(agent, tool_name, subject_kind)
    if own.is_file():
        return own
    parent = _SCHEMA_PARENT.get(agent)
    if parent:
        inherited = baseline_schema_path(parent, tool_name, subject_kind)
        if inherited.is_file():
            return inherited
    return own


def overlay_schema_path(agent: str, tool_name: str, subject_kind: str = 'tool') -> Path:
    """Per-user overlay path under `settings.payload_schemas_overlay_dir`."""
    return _overlay_dir() / agent / _schema_relpath(tool_name, subject_kind)


def schema_path_for(agent: str, tool_name: str) -> Path:
    """Deprecated alias kept for back-compat — returns the baseline path.
    Callers that mutate schemas should use `overlay_schema_path` instead."""
    return baseline_schema_path(agent, tool_name)


def _load_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


_LIST_UNION_KEYS = ('required', 'x-claude-versions')


def _merge_properties(base_props: dict, overlay_props: dict) -> dict:
    merged = dict(base_props)
    for name, schema in overlay_props.items():
        existing = merged.get(name)
        if isinstance(existing, dict) and isinstance(schema, dict):
            merged[name] = _merge_schemas(existing, schema)
        else:
            merged[name] = schema
    return merged


def _dedup_union(base: list, extra: list) -> list:
    out = list(base)
    for item in extra:
        if item not in out:
            out.append(item)
    return out


def _merge_schemas(baseline: dict, overlay: dict) -> dict:
    """Deep-merge overlay into baseline so user ratifies extend (rather
    than replace) the repo-shipped schema. `properties` recurses, the
    list-union keys (required, x-claude-versions) dedupe-union,
    everything else overlay-wins. Lets `git pull` add baseline fields
    while keeping local ratifies intact."""
    out = dict(baseline)
    for key, value in overlay.items():
        if key == 'properties' and isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge_properties(out[key], value)
        elif key in _LIST_UNION_KEYS and isinstance(value, list):
            out[key] = _dedup_union(out.get(key, []), value)
        else:
            out[key] = value
    return out


@functools.lru_cache(maxsize=128)
def _load_schema(agent: str, tool_name: str, subject_kind: str = 'tool') -> dict | None:
    """Return the merged schema for `(agent, tool_name, subject_kind)`, or None.

    Reads the baseline from the repo and overlays the per-user
    `settings.payload_schemas_overlay_dir` copy if present, deep-merging
    `properties` / `required` / `x-claude-versions` so user ratifies
    never block baseline upgrades from `git pull`."""
    baseline = _load_json(effective_baseline_path(agent, tool_name, subject_kind))
    overlay = _load_json(overlay_schema_path(agent, tool_name, subject_kind))
    if baseline is None and overlay is None:
        return None
    if overlay is None:
        return baseline
    if baseline is None:
        return overlay
    return _merge_schemas(baseline, overlay)


def _sample_repr(value: Any) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = repr(value)
    if len(text) > _SAMPLE_REPR_MAX:
        return text[:_SAMPLE_REPR_MAX] + '…'
    return text


def _format_path(parts: Iterable[Any]) -> str:
    """Render a jsonschema error path as `tool_input.questions[0].header`."""
    out = ''
    for part in parts:
        if isinstance(part, int):
            out += f'[{part}]'
        elif out:
            out += f'.{part}'
        else:
            out = str(part)
    return out


def _classify_error(err) -> str:
    validator = getattr(err, 'validator', None)
    if validator == 'required':
        return 'missing_required'
    if validator == 'type':
        return 'type_mismatch'
    if validator in ('enum', 'const'):
        return 'enum_violation'
    return 'type_mismatch'


def _jsonschema_findings(
    agent: str, tool_name: str, payload: dict, schema: dict,
    subject_kind: str = 'tool',
) -> list[DriftFinding]:
    validator = Draft202012Validator(schema)
    findings: list[DriftFinding] = []
    for err in validator.iter_errors(payload):
        kind = _classify_error(err)
        if kind == 'missing_required':
            missing = err.message.split("'")[1] if "'" in err.message else err.message
            path = _format_path(list(err.absolute_path) + [missing])
            expected = 'required'
        else:
            path = _format_path(err.absolute_path) or '(root)'
            expected = str(err.schema.get('type') or err.schema.get('const') or err.schema.get('enum') or '')
        findings.append(DriftFinding(
            agent=agent,
            tool_name=tool_name,
            drift_kind=kind,
            field_path=path,
            expected=expected or None,
            actual_sample=_sample_repr(err.instance),
            subject_kind=subject_kind,
        ))
    return findings


def _known_keys(props: dict) -> set[str]:
    """Snake_case forms of every property name in a schema's `properties`."""
    known: set[str] = set()
    for k in props.keys():
        known.add(k)
        known.add(_to_snake(k))
    return known


def _is_known_key(key: Any, known: set[str]) -> bool:
    if not isinstance(key, str):
        return True  # non-string keys can't be matched; skip flagging.
    return key in known or _to_snake(key) in known


def _is_opaque_object(schema: dict) -> bool:
    """An object schema with no `properties` is treated as a free-form dict
    (e.g. AskUserQuestion.answers keyed by question text, MCP tool_input
    whose shape is per-server). We don't descend into opaque objects."""
    return schema.get('type') == 'object' and not schema.get('properties')


def _walk_dict(
    payload: dict, schema: dict, path: str,
    agent: str, tool_name: str, findings: list[DriftFinding],
    subject_kind: str = 'tool',
) -> None:
    if _is_opaque_object(schema):
        return
    props = schema.get('properties') or {}
    known = _known_keys(props)
    # Top-level payload keys include the always-present envelope fields;
    # which envelope set depends on the subject_kind axis. Directly under
    # `tool_response`, the canonical single-blob `output` key is always known.
    if not path:
        known = known | _envelope_keys(subject_kind)
    elif path == 'tool_response':
        known = known | _RESULT_ENVELOPE_KEYS
    for key, value in payload.items():
        full = f'{path}.{key}' if path else key
        if not _is_known_key(key, known):
            findings.append(DriftFinding(
                agent=agent,
                tool_name=tool_name,
                drift_kind='unknown_field',
                field_path=full,
                expected=None,
                actual_sample=_sample_repr(value),
                subject_kind=subject_kind,
            ))
            continue
        sub_schema = props.get(key) or props.get(_to_snake(key))
        _walk_unknown_fields(value, sub_schema, full, agent, tool_name, findings, subject_kind)


def _walk_unknown_fields(
    payload: Any, schema: Any, path: str,
    agent: str, tool_name: str, findings: list[DriftFinding],
    subject_kind: str = 'tool',
) -> None:
    if not isinstance(schema, dict):
        return
    if isinstance(payload, dict):
        _walk_dict(payload, schema, path, agent, tool_name, findings, subject_kind)
    elif isinstance(payload, list):
        items_schema = schema.get('items')
        for i, item in enumerate(payload):
            _walk_unknown_fields(
                item, items_schema, f'{path}[{i}]', agent, tool_name, findings, subject_kind)


def _is_postool_event(payload: dict) -> bool:
    event = payload.get('hook_event_name') or payload.get('hookEventName')
    return event == 'PostToolUse'


def validate(
    tool_name: str | None,
    payload: dict,
    agent: str = _DEFAULT_AGENT,
) -> list[DriftFinding]:
    """Return all drift findings for `payload`. Never raises.

    Guards:
      * Caller passes the *normalized* payload (snake_case top-level
        keys). For raw provider payloads, normalize first via
        `HookPayload.from_stdin_json` / `_normalize_payload`.
      * Returns `[]` for any non-PostToolUse event so the catch-all
        handler can pass everything through without an outer event gate.
      * `agent` selects which schema directory to read from
        (`payload_schemas/<agent>/`). Defaults to `claude`; future
        provider integrations pass their own provider id.
    """
    if not isinstance(payload, dict) or not _is_postool_event(payload):
        return []
    if not tool_name:
        return []

    schema = _load_schema(agent, tool_name)
    if schema is None:
        return [DriftFinding(
            agent=agent,
            tool_name=tool_name,
            drift_kind='unknown_tool',
            field_path='(root)',
            expected=None,
            actual_sample=_sample_repr({'tool_name': tool_name}),
        )]

    findings = _jsonschema_findings(agent, tool_name, payload, schema)
    _walk_unknown_fields(payload, schema, '', agent, tool_name, findings)
    return findings


def validate_event(
    event_name: str | None,
    payload: dict,
    agent: str = _DEFAULT_AGENT,
) -> list[DriftFinding]:
    """Return all drift findings for a hook-event `payload`. Never raises.

    The hook-event analog of `validate`: validates against the
    `subject_kind='hook_event'` schema axis (`payload_schemas/<agent>/_hooks/`).
    Returns `[]` for a non-dict payload or a falsy event name. An unknown
    event (no baseline/overlay schema) yields a single `unknown_event`
    finding; otherwise jsonschema + the unknown-field walker run with every
    finding tagged `subject_kind='hook_event'`."""
    if not isinstance(payload, dict) or not event_name:
        return []

    schema = _load_schema(agent, event_name, subject_kind='hook_event')
    if schema is None:
        return [DriftFinding(
            agent=agent,
            tool_name=event_name,
            drift_kind='unknown_event',
            field_path='(root)',
            expected=None,
            actual_sample=_sample_repr({'hook_event_name': event_name}),
            subject_kind='hook_event',
        )]

    findings = _jsonschema_findings(
        agent, event_name, payload, schema, subject_kind='hook_event')
    _walk_unknown_fields(
        payload, schema, '', agent, event_name, findings, subject_kind='hook_event')
    return findings
