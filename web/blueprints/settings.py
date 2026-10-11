"""Settings GET/POST endpoints.

Repo registration moved to ``web.blueprints.repos`` (the /repos page
manages it explicitly). The legacy ``/api/settings/rescan`` endpoint
was removed alongside the auto-discovery model.
"""

from dataclasses import asdict

from flask import Blueprint, request, jsonify

from lib.auth import require_editor
from lib.providers import (
    list_visible_provider_ids, build_provider, is_provider_id, active_provider_id,
)
from lib.settings import (
    get_current_values, save_settings, settings_file_lock, _load_settings,
    SETTINGS_SCHEMA,
    ProviderConfig, ProviderPathOverrides,
)
from web.blueprints.settings_fields import (  # noqa: F401
    _agent_bridge_warnings, _settings_blocks, _tasks_warnings,
)


settings_bp = Blueprint('settings', __name__)


def _to_bool(val) -> bool:
    if isinstance(val, str):
        return val.strip().lower() in ('true', '1', 'yes', 'on')
    return bool(val)


def _coerce_number(val, kind: str):
    try:
        return int(val) if kind == 'int' else float(val)
    except (TypeError, ValueError):
        return None


def _bounded(field: dict, num) -> "str | None":
    lo, hi = field.get('min'), field.get('max')
    if lo is not None and num < lo:
        return f"{field['key']} must be ≥ {lo}"
    if hi is not None and num > hi:
        return f"{field['key']} must be ≤ {hi}"
    return None


def _coerce_list(field: dict, val) -> tuple:
    """Strip each entry and drop the empties → list[str]."""
    if not isinstance(val, list):
        return None, f"{field['key']} must be a list"
    return [s for v in val if (s := str(v).strip())], None


def _coerce_choice(field: dict, val) -> tuple:
    sval = str(val)
    if sval in field.get('options', []):
        return sval, None
    return None, f"{field['key']} must be one of {field.get('options')}"


def _coerce_scalar(field: dict, val) -> tuple:
    """(value, error) for one field — exactly one is non-None."""
    kind = field['type']
    if kind == 'bool':
        return _to_bool(val), None
    if kind == 'string':
        return ('' if val is None else str(val).strip()), None
    if kind == 'list':
        return _coerce_list(field, val)
    if kind == 'choice':
        return _coerce_choice(field, val)
    num = _coerce_number(val, kind)
    if num is None:
        return None, f"{field['key']} must be a number"
    err = _bounded(field, num)
    if err:
        return None, err
    # A `null_as` sentinel (e.g. -1) is how the UI represents the model's None
    # in a plain number input; fold it back to None so persisted settings stay
    # canonical (the inverse of the GET-side substitution in _field_payload).
    if field.get('null_as') is not None and num == field['null_as']:
        return None, None
    return num, None


def _coerce_block(fields: list[dict], body: dict) -> "tuple[dict, list[str]]":
    """Coerce + validate only the exposed fields present in `body`."""
    updates: dict = {}
    errors: list[str] = []
    for field in fields:
        if field['key'] not in body:
            continue
        value, err = _coerce_scalar(field, body[field['key']])
        if err:
            errors.append(err)
        else:
            updates[field['key']] = value
    return updates, errors


def _field_payload(current, defaults, field: dict) -> dict:
    """One field's metadata + live value + default, mapping a None model value
    to the field's `null_as` sentinel so a plain number input can represent it
    (the inverse of the PUT-side fold in _coerce_scalar)."""
    value = getattr(current, field["key"])
    default = getattr(defaults, field["key"])
    sentinel = field.get("null_as")
    if sentinel is not None:
        value = sentinel if value is None else value
        default = sentinel if default is None else default
    return {**field, "value": value, "default": default}


def _block_get(name: str):
    """Field metadata + current value + default for one registered block."""
    block = _settings_blocks().get(name)
    if block is None:
        return jsonify({'error': 'unknown settings block'}), 404
    from lib.settings import settings as _settings
    current = getattr(_settings, block['attr'])
    defaults = block['model']()
    fields = [_field_payload(current, defaults, f) for f in block['fields']]
    warn = block.get('warn')
    return jsonify({"fields": fields,
                    "warnings": warn(current) if warn else []})


def _scope_block_base(attr: str, scope: str) -> dict:
    """The block as it takes effect, so a partial PUT merges against every
    field already set and cannot drop the ones it did not send.

    `_load_settings()` shallow-merges the two files (`{**shared, **local}`), so
    for a block present in BOTH it returns only the *local* copy — not what
    pydantic-settings loads, which deep-merges them. When the local file sets
    the block, `save_settings` writes the whole value there, so the base must
    carry the shared-only fields too; otherwise the shared file alone is it."""
    from lib import settings as _cfg
    local = _cfg._load_json(_cfg.SETTINGS_LOCAL_PATH)
    if scope == 'local':
        return dict(local.get(attr) or {})
    shared = dict(_cfg._load_json(_cfg.SETTINGS_PATH).get(attr) or {})
    if attr not in local:
        return shared
    return _cfg._deep_merge(shared, local.get(attr) or {})


def _block_put(name: str):
    """Validate + persist edited fields for one block, preserving unexposed
    on-disk overrides, and reload the singleton so it applies live."""
    block = _settings_blocks().get(name)
    if block is None:
        return jsonify({'ok': False, 'error': 'unknown settings block'}), 404
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({'ok': False, 'error': 'invalid JSON body'}), 400
    updates, errors = _coerce_block(block['fields'], body)
    if errors:
        return jsonify({'ok': False, 'errors': errors}), 400
    merged = _scope_block_base(block['attr'], block['scope'])
    merged.update(updates)
    try:
        block['model'](**merged)  # full pydantic re-validation
    except Exception as exc:
        return jsonify({'ok': False, 'errors': [str(exc)]}), 400
    save_settings({block['attr']: merged}, scope=block['scope'])
    return jsonify({'ok': True, 'msg': f"{block['label']} saved."})


@settings_bp.route('/api/settings/agent-memory')
def api_agent_memory_settings():
    return _block_get('agent-memory')


@settings_bp.route('/api/settings/agent-memory', methods=['PUT'])
@require_editor
def api_update_agent_memory_settings():
    return _block_put('agent-memory')


@settings_bp.route('/api/settings/agent-messages')
def api_agent_messages_settings():
    return _block_get('agent-messages')


@settings_bp.route('/api/settings/agent-messages', methods=['PUT'])
@require_editor
def api_update_agent_messages_settings():
    return _block_put('agent-messages')


@settings_bp.route('/api/settings/agent-bridge')
def api_agent_bridge_settings():
    return _block_get('agent-bridge')


@settings_bp.route('/api/settings/agent-bridge', methods=['PUT'])
@require_editor
def api_update_agent_bridge_settings():
    return _block_put('agent-bridge')


@settings_bp.route('/api/settings/topic-evolution')
def api_topic_evolution_settings():
    return _block_get('topic-evolution')


@settings_bp.route('/api/settings/topic-evolution', methods=['PUT'])
@require_editor
def api_update_topic_evolution_settings():
    return _block_put('topic-evolution')


@settings_bp.route('/api/settings/tasks')
def api_tasks_settings():
    return _block_get('tasks')


@settings_bp.route('/api/settings/tasks', methods=['PUT'])
@require_editor
def api_update_tasks_settings():
    return _block_put('tasks')


@settings_bp.route('/api/settings/task-integrations')
def api_task_integrations_settings():
    return _block_get('task-integrations')


@settings_bp.route('/api/settings/task-integrations', methods=['PUT'])
@require_editor
def api_update_task_integrations_settings():
    return _block_put('task-integrations')


@settings_bp.route('/api/settings/agent-sdk')
def api_agent_sdk_settings():
    return _block_get('agent-sdk')


@settings_bp.route('/api/settings/agent-sdk', methods=['PUT'])
@require_editor
def api_update_agent_sdk_settings():
    return _block_put('agent-sdk')


@settings_bp.route('/api/settings')
def api_settings():
    return jsonify(get_current_values())


def _coerce_setting_value(default, val):
    """Coerce one POSTed flat-settings value to the type of its schema
    default (list/bool/int/str). `bool` is checked before `int` because
    `bool` subclasses `int`, so the int branch would otherwise match and
    `int("false")` would raise + fall through to a raw string."""
    if isinstance(default, list):
        return [v.strip() for v in val if v.strip()] if isinstance(val, list) else val
    if isinstance(default, bool):
        if isinstance(val, str):
            return val.strip().lower() in ('true', '1', 'yes', 'on')
        return bool(val)
    if isinstance(default, int):
        try:
            return int(val)
        except (ValueError, TypeError):
            return val
    return val


@settings_bp.route('/api/settings', methods=['POST'])
@require_editor
def api_settings_save():
    data = request.get_json(silent=True) or {}
    updates = {}
    with settings_file_lock():
        for key, default, _ in SETTINGS_SCHEMA:
            if key not in data:
                existing = _load_settings()
                existing.pop(key, None)
                save_settings(existing)
                continue
            updates[key] = _coerce_setting_value(default, data[key])
        save_settings(updates)
    return jsonify({'ok': True, 'msg': 'Settings saved. Restart server to apply.'})


# ── Provider settings (multi-provider enablement + per-provider overrides) ──

# Derived from the typed model so a new path override can't be added there
# without the endpoint picking it up (and vice versa).
_PROVIDER_PATH_FIELDS = list(ProviderPathOverrides.model_fields)


def _provider_default_paths(provider) -> dict:
    """Resolved default paths for a provider when no override is set."""
    try:
        return {
            'skills_dir': str(provider.global_skills_dir()),
            'plans_dir': str(provider.plans_dir()),
            'traces_dir': str(provider.traces_dir()),
            'hook_settings_path': str(provider.hook_settings_path()),
            'hook_manager_config_path': str(provider.hook_manager_config_path()),
            'hook_payload_log_path': str(provider.hook_payload_log_path()),
            'transcript_projects_dir': str(provider.transcript_projects_dir()),
        }
    except Exception:
        return {field: '' for field in _PROVIDER_PATH_FIELDS}


def _handler_defaults() -> list[dict]:
    """Registry-defined handler metadata for the provider-config UI.

    Returns default priorities independent of any per-provider overrides so
    the UI can show "default N" when editing overrides.
    """
    from hook_manager.registry import REGISTRY
    return [
        {
            'name': h.name,
            'label': h.label,
            'kind': h.kind,
            'events': list(h.events),
            'default_priority': h.priority,
        }
        for h in REGISTRY
    ]


def _provider_config_row(provider_id: str) -> dict:
    """Current settings-based config for one provider, plus derived metadata."""
    merged = _load_settings()
    providers_cfg = merged.get('providers') or {}
    cfg = providers_cfg.get(provider_id) or {}
    if isinstance(cfg, ProviderConfig):
        cfg = cfg.model_dump()
    elif not isinstance(cfg, dict):
        cfg = {}

    provider = build_provider(provider_id)
    default_paths = _provider_default_paths(provider)
    active = active_provider_id()

    return {
        'id': provider_id,
        'name': provider.display_name,
        'active': provider_id == active,
        'enabled': bool(cfg.get('enabled', provider_id == active)),
        'capabilities': asdict(provider.capabilities),
        'default_paths': default_paths,
        'path_overrides': {
            field: cfg.get(field) if cfg.get(field) not in (None, '') else None
            for field in _PROVIDER_PATH_FIELDS
        },
        'disabled_handlers': list(cfg.get('disabled_handlers') or []),
        'priority_overrides': dict(cfg.get('priority_overrides') or {}),
    }


@settings_bp.route('/api/settings/providers')
def api_provider_settings():
    return jsonify({
        'providers': [_provider_config_row(pid) for pid in list_visible_provider_ids()],
        'handler_defaults': _handler_defaults(),
    })


def _coerce_priority_overrides(pid: str, overrides: dict, errors: list) -> dict:
    """Coerce `{handler: priority}` to ints, appending per-key errors."""
    cleaned: dict = {}
    for k, v in overrides.items():
        if not k:
            continue
        try:
            cleaned[str(k)] = int(v)
        except (TypeError, ValueError):
            errors.append(f'{pid}.priority_overrides.{k} must be an integer')
    return cleaned


def _clean_path_overrides(raw: dict) -> dict:
    """Path-override fields present in `raw`, normalized ('' → None)."""
    out: dict = {}
    for field in _PROVIDER_PATH_FIELDS:
        if field in raw:
            val = raw[field]
            out[field] = None if val in (None, '') else str(val)
    return out


def _clean_provider_entry(pid: str, raw: dict, errors: list) -> dict:
    """Coerce one provider's POST body into a settings entry, collecting
    validation problems in `errors`. Only keys present in `raw` are written,
    so partial updates leave untouched fields alone."""
    entry: dict = _clean_path_overrides(raw)
    if 'enabled' in raw:
        entry['enabled'] = bool(raw['enabled'])

    if 'disabled_handlers' in raw:
        handlers = raw['disabled_handlers']
        if isinstance(handlers, list):
            entry['disabled_handlers'] = [str(h) for h in handlers if h]
        else:
            errors.append(f'{pid}.disabled_handlers must be a list')

    if 'priority_overrides' in raw:
        overrides = raw['priority_overrides']
        if isinstance(overrides, dict):
            entry['priority_overrides'] = _coerce_priority_overrides(pid, overrides, errors)
        else:
            errors.append(f'{pid}.priority_overrides must be an object')

    return entry


def _collect_provider_entries(providers_update: dict, existing: dict) -> tuple:
    """(cleaned_entries, errors) for the POSTed provider map, each entry
    merged over its existing on-disk config so partial updates accrete."""
    cleaned: dict = {}
    errors: list[str] = []
    for pid, raw in providers_update.items():
        if not is_provider_id(pid):
            errors.append(f'unknown provider: {pid}')
            continue
        if not isinstance(raw, dict):
            errors.append(f'{pid}: expected object')
            continue
        entry = _clean_provider_entry(pid, raw, errors)
        if entry:
            cleaned[pid] = {**(existing.get(pid) or {}), **entry}
    return cleaned, errors


def _nonempty_provider_configs(providers: dict) -> dict:
    """Drop entries that carry only defaults so we don't persist noise."""
    return {
        pid: cfg for pid, cfg in providers.items()
        if cfg and any(v not in (None, [], {}, '') for v in cfg.values())
    }


def _first_invalid_provider(providers: dict) -> "str | None":
    """Validate each config against the typed model; first error or None."""
    for pid, cfg in providers.items():
        try:
            ProviderConfig(**cfg)
        except Exception as exc:
            return f'{pid}: {exc}'
    return None


@settings_bp.route('/api/settings/providers', methods=['PUT'])
@require_editor
def api_update_provider_settings():
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict) or 'providers' not in body:
        return jsonify({'ok': False, 'error': 'providers object required'}), 400

    providers_update = body['providers']
    if not isinstance(providers_update, dict):
        return jsonify({'ok': False, 'error': 'providers must be an object'}), 400

    existing = _load_settings().get('providers') or {}
    if not isinstance(existing, dict):
        existing = {}

    cleaned, errors = _collect_provider_entries(providers_update, existing)
    if errors:
        return jsonify({'ok': False, 'errors': errors}), 400

    # Preserve providers that were not sent (partial updates), then drop
    # entries left holding only defaults.
    new_providers = _nonempty_provider_configs({**existing, **cleaned})

    invalid = _first_invalid_provider(new_providers)
    if invalid:
        return jsonify({'ok': False, 'errors': [invalid]}), 400

    save_settings({'providers': new_providers}, scope='local')
    return jsonify({'ok': True, 'msg': 'Provider settings saved.'})
