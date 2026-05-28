"""Settings GET/POST endpoints.

Repo registration moved to ``web.blueprints.repos`` (the /repos page
manages it explicitly). The legacy ``/api/settings/rescan`` endpoint
was removed alongside the auto-discovery model.
"""

from flask import Blueprint, request, jsonify

from lib.auth import require_editor
from lib.settings import (
    get_current_values, save_settings, _load_settings, SETTINGS_SCHEMA,
)


settings_bp = Blueprint('settings', __name__)


@settings_bp.route('/api/settings')
def api_settings():
    return jsonify(get_current_values())


@settings_bp.route('/api/settings', methods=['POST'])
@require_editor
def api_settings_save():
    data = request.get_json(silent=True) or {}
    updates = {}
    for key, default, _ in SETTINGS_SCHEMA:
        if key not in data:
            existing = _load_settings()
            existing.pop(key, None)
            save_settings(existing)
            continue
        val = data[key]
        if isinstance(default, list):
            updates[key] = [v.strip() for v in val if v.strip()] if isinstance(val, list) else val
        elif isinstance(default, bool):
            # Must come before the int branch: `bool` is a subclass of
            # `int` in Python, so the int check would otherwise match
            # and `int("false")` would raise + fall through to a string.
            if isinstance(val, str):
                updates[key] = val.strip().lower() in ('true', '1', 'yes', 'on')
            else:
                updates[key] = bool(val)
        elif isinstance(default, int):
            try:
                updates[key] = int(val)
            except (ValueError, TypeError):
                updates[key] = val
        else:
            updates[key] = val
    save_settings(updates)
    return jsonify({'ok': True, 'msg': 'Settings saved. Restart server to apply.'})
