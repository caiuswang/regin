"""API key CRUD — admin only.

Minting a credential that authenticates without a login is an admin act, so
every route here is `require_role('admin')` on top of the app-wide JWT gate;
none of them is in PUBLIC_API_ENDPOINTS. A key cannot manage keys: nothing on
the `require_scope` path produces a user, so an `rgn_…` bearer fails the role
check here exactly as it fails every other user-gated route.

The plaintext key is in the creation response and nowhere else — the table
stores only its SHA-256, so a lost key is re-minted rather than recovered.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from lib import api_keys
from lib.auth import get_current_user, require_role

api_keys_bp = Blueprint('api_keys', __name__)

_NAME_MAX = 200


@api_keys_bp.route('/api/api-keys', methods=['GET'])
@require_role('admin')
def api_list_api_keys():
    """Every key's metadata. Never carries key material."""
    return jsonify({"keys": api_keys.list_keys()})


@api_keys_bp.route('/api/api-keys', methods=['POST'])
@require_role('admin')
def api_create_api_key():
    """Mint a key. `key` in this response is the only copy that will exist."""
    payload = request.get_json(silent=True) or {}
    name = payload.get("name")
    name = name.strip()[:_NAME_MAX] if isinstance(name, str) else ""
    if not name:
        return jsonify({"error": "name required"}), 400
    scopes = api_keys.normalize_scopes(payload.get("scopes"))
    if not scopes:
        return jsonify({"error": "at least one known scope required"}), 400
    user = get_current_user()
    raw, view = api_keys.mint_key(name, scopes, created_by=user["id"])
    return jsonify({"key": raw, "api_key": view}), 201


@api_keys_bp.route('/api/api-keys/<int:key_id>', methods=['DELETE'])
@require_role('admin')
def api_revoke_api_key(key_id: int):
    """Revoke a key. Idempotent from the caller's side: an already-revoked or
    unknown id is a 404, and the key stays refused either way."""
    if not api_keys.revoke_key(key_id):
        return jsonify({"error": "unknown or already revoked key"}), 404
    return jsonify({"ok": True})
