"""Authentication, user management, and audit endpoints."""

from flask import Blueprint, request, jsonify

from lib.auth import (
    require_auth, require_role, get_current_user,
    authenticate, register_user, user_count, list_users, get_user,
    update_user, change_password, set_role, delete_user,
)
from lib.settings import settings
from lib import audit
from lib.utils.pagination import Page, clamp_page, clamp_size


auth_bp = Blueprint('auth', __name__)


# ── Authentication ──────────────────────────────────────────────

@auth_bp.route('/api/auth/login', methods=['POST'])
def api_auth_login():
    data = request.get_json(silent=True) or {}
    username = (data.get('username') or '').strip()
    password = data.get('password', '')
    if not username or not password:
        return jsonify({'error': 'Username and password required'}), 400
    result = authenticate(username, password)
    if not result:
        return jsonify({'error': 'Invalid credentials'}), 401
    return jsonify(result)


def _authorize_registration(data):
    """Gate self-service registration once the system is bootstrapped.

    Registration is open ONLY for first-run bootstrap (no users yet).
    Once any account exists, creating users is admin-only — this endpoint
    is network-public (see PUBLIC_API_ENDPOINTS), so without this branch
    anyone could mint themselves an editor account.

    Returns ``(error_response, None)`` to abort, or ``(None, role)`` to
    proceed with the resolved role (``None`` = registrar's default).
    """
    if user_count() == 0:
        return None, None
    actor = get_current_user()
    if actor is None:
        return (jsonify({'error': 'Authentication required'}), 401), None
    if actor['role'] != 'admin':
        return (jsonify({'error': 'Admin role required to create users'}), 403), None
    requested = (data.get('role') or '').strip().lower()
    if requested and requested not in ('admin', 'editor', 'viewer'):
        return (jsonify({'error': 'Invalid role'}), 400), None
    return None, (requested or None)


@auth_bp.route('/api/auth/register', methods=['POST'])
def api_auth_register():
    data = request.get_json(silent=True) or {}
    username = (data.get('username') or '').strip()
    display_name = (data.get('display_name') or username).strip()
    password = data.get('password', '')
    email = data.get('email')

    err, role = _authorize_registration(data)
    if err is not None:
        return err

    if not username or not password:
        return jsonify({'error': 'Username and password required'}), 400
    if len(password) < 4:
        return jsonify({'error': 'Password must be at least 4 characters'}), 400
    try:
        user = register_user(username, display_name, password, email=email, role=role)
        return jsonify({'ok': True, 'user': user,
                        'msg': f"Registered as {user['role']}"})
    except Exception as exc:
        return jsonify({'error': str(exc)}), 409


@auth_bp.route('/api/auth/me')
def api_auth_me():
    user = get_current_user()
    if not user:
        return jsonify({'user': None, 'needs_setup': user_count() == 0, 'mode': settings.mode})
    return jsonify({'user': user, 'needs_setup': False, 'mode': settings.mode})


@auth_bp.route('/api/auth/change-password', methods=['POST'])
@require_auth
def api_change_password():
    user = get_current_user()
    data = request.get_json(silent=True) or {}
    old_pw = data.get('old_password', '')
    new_pw = data.get('new_password', '')
    if not old_pw or not new_pw:
        return jsonify({'error': 'Both old and new password required'}), 400
    if len(new_pw) < 4:
        return jsonify({'error': 'Password must be at least 4 characters'}), 400
    if not change_password(user['id'], old_pw, new_pw):
        return jsonify({'error': 'Current password is incorrect'}), 400
    return jsonify({'ok': True, 'msg': 'Password changed'})


@auth_bp.route('/api/auth/profile', methods=['POST'])
@require_auth
def api_update_profile():
    user = get_current_user()
    data = request.get_json(silent=True) or {}
    display_name = data.get('display_name')
    email = data.get('email')
    update_user(user['id'], display_name=display_name, email=email)
    return jsonify({'ok': True, 'msg': 'Profile updated'})


# ── User management ────────────────────────────────────────────

@auth_bp.route('/api/users')
@require_auth
def api_list_users():
    return jsonify(list_users())


@auth_bp.route('/api/users/<int:user_id>/role', methods=['POST'])
@require_auth
@require_role('admin')
def api_set_user_role(user_id):
    data = request.get_json(silent=True) or {}
    role = data.get('role', '')
    if role not in ('admin', 'editor', 'viewer'):
        return jsonify({'error': 'Invalid role'}), 400
    user = get_current_user()
    if user_id == user['id']:
        return jsonify({'error': 'Cannot change your own role'}), 400
    if not set_role(user_id, role):
        return jsonify({'error': 'User not found'}), 404
    audit.log_action(user['id'], user['username'], 'set_role',
                     f'user:{user_id}', {'role': role})
    return jsonify({'ok': True, 'msg': f'Role set to {role}'})


@auth_bp.route('/api/users/<int:user_id>/delete', methods=['POST'])
@require_auth
@require_role('admin')
def api_delete_user(user_id):
    user = get_current_user()
    if user_id == user['id']:
        return jsonify({'error': 'Cannot delete yourself'}), 400
    target = get_user(user_id)
    if not target:
        return jsonify({'error': 'User not found'}), 404
    delete_user(user_id)
    audit.log_action(user['id'], user['username'], 'delete_user',
                     f'user:{target["username"]}')
    return jsonify({'ok': True, 'msg': f'Deleted user {target["username"]}'})


# ── Audit log ──────────────────────────────────────────────────

@auth_bp.route('/api/audit')
def api_audit_log():
    """Offset-paginated audit log.

    Offset is fine here: the audit log grows slowly, users usually want
    "jump to page N" + a visible total, and concurrent writes are rare
    (one per dashboard mutation). ``created_at DESC, id DESC`` is a
    stable sort so the boundary between adjacent pages doesn't flicker
    when two actions land in the same second.
    """
    page_idx = clamp_page(request.args.get('page'))
    size = clamp_size(request.args.get('size'), default=50)
    user = request.args.get('user')
    action = request.args.get('action')
    items, total = audit.get_log_page(
        page=page_idx, size=size, user=user, action=action,
    )
    result = Page(
        items=items, total=total, page=page_idx, size=size,
        has_next=(page_idx + 1) * size < total,
        has_prev=page_idx > 0,
    )
    return jsonify(result.to_envelope())
