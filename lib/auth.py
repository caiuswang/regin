"""JWT-based authentication for the regin web dashboard.

User accounts live in the primary SQLite file in standalone mode, or
in a shared MySQL database (`settings.database_url`) in shared mode.
Both paths are routed through `lib.orm.AuthSessionLocal()` — the User
and AuditLog SQLModel classes declare a schema that works on either
dialect.

Password storage: PBKDF2-HMAC-SHA256 with a random per-user salt,
stored as `<salt_hex>:<dk_hex>`.

JWTs: HS256, 1-week expiry, signed with a per-install secret in
`config/jwt_secret.txt` (gitignored; auto-generated on first boot).
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from datetime import datetime, timezone
from functools import wraps
from typing import Optional

import jwt
from flask import g, jsonify, request
from sqlmodel import select

from lib.activity_log import get_activity_logger as _get_activity_logger


def _auth_log():
    return _get_activity_logger("auth")

from lib.orm import AuthSessionLocal
from lib.orm.models import User


# JWT secret — generated once and cached in config/jwt_secret.txt (gitignored).
_SECRET_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config", "jwt_secret.txt",
)
_JWT_ALGORITHM = "HS256"
_JWT_EXPIRY_SECONDS = 7 * 24 * 3600  # 1 week


def _get_secret() -> str:
    """Load or generate the JWT signing secret."""
    try:
        with open(_SECRET_PATH) as f:
            return f.read().strip()
    except FileNotFoundError:
        secret = secrets.token_hex(32)
        os.makedirs(os.path.dirname(_SECRET_PATH), exist_ok=True)
        with open(_SECRET_PATH, "w") as f:
            f.write(secret)
        return secret


def hash_password(plain: str) -> str:
    """Hash a password with PBKDF2-HMAC-SHA256."""
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", plain.encode(), salt, 100_000)
    return salt.hex() + ":" + dk.hex()


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a password against a PBKDF2 hash."""
    try:
        salt_hex, dk_hex = hashed.split(":", 1)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(dk_hex)
        actual = hashlib.pbkdf2_hmac("sha256", plain.encode(), salt, 100_000)
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def create_token(user_id: int, username: str, role: str) -> str:
    """Create a signed JWT token."""
    payload = {
        "sub": str(user_id),
        "username": username,
        "role": role,
        "iat": int(time.time()),
        "exp": int(time.time()) + _JWT_EXPIRY_SECONDS,
    }
    return jwt.encode(payload, _get_secret(), algorithm=_JWT_ALGORITHM)


def verify_token(token: str) -> Optional[dict]:
    """Decode and verify a JWT token. Returns payload or None."""
    try:
        payload = jwt.decode(token, _get_secret(), algorithms=[_JWT_ALGORITHM])
        payload["sub"] = int(payload["sub"])
        return payload
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError, ValueError):
        return None


def get_current_user() -> Optional[dict]:
    """Extract the current user from the Authorization header."""
    if hasattr(g, "_current_user"):
        return g._current_user

    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        g._current_user = None
        return None

    payload = verify_token(auth[7:])
    if not payload:
        g._current_user = None
        return None

    g._current_user = {
        "id": payload["sub"],
        "username": payload["username"],
        "role": payload["role"],
    }
    return g._current_user


def require_auth(f):
    """Decorator: reject requests without a valid JWT."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        user = get_current_user()
        if not user:
            return jsonify({"error": "Authentication required"}), 401
        return f(*args, **kwargs)
    return wrapper


def require_editor(f):
    """Decorator: reject requests from viewers (need editor or admin role)."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        user = get_current_user()
        if not user:
            return jsonify({"error": "Authentication required"}), 401
        if user["role"] == "viewer":
            return jsonify({"error": "Editor role required"}), 403
        return f(*args, **kwargs)
    return wrapper


def require_role(role: str):
    """Decorator factory: reject requests from users without the given role."""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            user = get_current_user()
            if not user:
                return jsonify({"error": "Authentication required"}), 401
            if user["role"] != role and user["role"] != "admin":
                return jsonify({"error": f'Role "{role}" required'}), 403
            return f(*args, **kwargs)
        return wrapper
    return decorator


# ── User CRUD (via SQLModel) ──────────────────────────────────

def _user_to_dict(u: User, *, include_hash: bool = False) -> dict:
    """Project a User row into the legacy dict shape the web layer expects."""
    out: dict = {
        "id": u.id,
        "username": u.username,
        "display_name": u.display_name,
        "email": u.email,
        "role": u.role,
        "created_at": u.created_at,
        "last_login": u.last_login,
    }
    if include_hash:
        out["password_hash"] = u.password_hash
    return out


def user_count() -> int:
    """Return number of registered users. Returns 0 on DB failure
    (used during bootstrap to detect "setup needed"; any error means
    "treat as unconfigured")."""
    try:
        with AuthSessionLocal() as session:
            return len(session.exec(select(User.id)).all())
    except Exception:
        return 0


def register_user(username: str, display_name: str, password: str,
                   email: Optional[str] = None, role: Optional[str] = None) -> dict:
    """Register a new user. First user becomes admin."""
    if role is None:
        role = "admin" if user_count() == 0 else "editor"

    pw_hash = hash_password(password)
    with AuthSessionLocal() as session:
        user = User(
            username=username, display_name=display_name, email=email,
            password_hash=pw_hash, role=role,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        user_view = {
            "id": user.id,
            "username": user.username,
            "display_name": user.display_name,
            "role": user.role,
        }
    _auth_log().write(
        "user_registered",
        user_id=user_view["id"], username=user_view["username"],
        role=user_view["role"], email=email,
    )
    return user_view


def list_users() -> list[dict]:
    """Return all users (without password hashes)."""
    with AuthSessionLocal() as session:
        rows = session.exec(select(User).order_by(User.id)).all()
        return [_user_to_dict(u) for u in rows]


def get_user(user_id: int) -> Optional[dict]:
    """Return a single user by ID (without password hash)."""
    with AuthSessionLocal() as session:
        u = session.get(User, user_id)
        return _user_to_dict(u) if u else None


def update_user(user_id: int, display_name: Optional[str] = None,
                 email: Optional[str] = None) -> bool:
    """Update a user's profile fields. Returns False if nothing to update."""
    if display_name is None and email is None:
        return False
    with AuthSessionLocal() as session:
        u = session.get(User, user_id)
        if u is None:
            _auth_log().warn("user_profile_update_rejected",
                             user_id=user_id, reason="unknown_user")
            return False
        if display_name is not None:
            u.display_name = display_name
        if email is not None:
            u.email = email
        session.add(u)
        session.commit()
    _auth_log().write(
        "user_profile_updated", user_id=user_id,
        updated_fields=[k for k, v in
                        (("display_name", display_name), ("email", email))
                        if v is not None],
    )
    return True


def change_password(user_id: int, old_password: str, new_password: str) -> bool:
    """Change a user's password (requires current password)."""
    with AuthSessionLocal() as session:
        u = session.get(User, user_id)
        if u is None or not verify_password(old_password, u.password_hash):
            from lib.activity_log import get_activity_logger
            get_activity_logger('auth').warn(
                'password_change_rejected', user_id=user_id,
                reason='invalid_current_password' if u else 'unknown_user',
            )
            return False
        u.password_hash = hash_password(new_password)
        session.add(u)
        session.commit()
    _auth_log().write('password_changed', user_id=user_id)
    return True


def reset_password(username: str, new_password: str) -> bool:
    """Force-reset a user's password without requiring the old one (CLI only)."""
    with AuthSessionLocal() as session:
        u = session.exec(select(User).where(User.username == username)).first()
        if u is None:
            _auth_log().warn("password_reset_rejected",
                             username=username, reason="unknown_user")
            return False
        u.password_hash = hash_password(new_password)
        session.add(u)
        session.commit()
        user_id = u.id
    _auth_log().write("password_reset", username=username, user_id=user_id)
    return True


def set_role(user_id: int, role: str) -> bool:
    """Set a user's role (admin only)."""
    if role not in ("admin", "editor", "viewer"):
        _auth_log().warn("role_change_rejected",
                         user_id=user_id, role=role, reason="invalid_role")
        return False
    with AuthSessionLocal() as session:
        u = session.get(User, user_id)
        if u is None:
            _auth_log().warn("role_change_rejected",
                             user_id=user_id, role=role, reason="unknown_user")
            return False
        previous_role = u.role
        u.role = role
        session.add(u)
        session.commit()
    _auth_log().write("role_changed", user_id=user_id,
                      role=role, previous_role=previous_role)
    return True


def delete_user(user_id: int) -> bool:
    """Delete a user account."""
    with AuthSessionLocal() as session:
        u = session.get(User, user_id)
        if u is None:
            _auth_log().warn("user_delete_rejected",
                             user_id=user_id, reason="unknown_user")
            return False
        username = u.username
        session.delete(u)
        session.commit()
    _auth_log().write("user_deleted", user_id=user_id, username=username)
    return True


def authenticate(username: str, password: str) -> Optional[dict]:
    """Verify credentials and return user dict + token, or None."""
    from lib.activity_log import get_activity_logger
    auth_log = get_activity_logger('auth')
    with AuthSessionLocal() as session:
        u = session.exec(select(User).where(User.username == username)).first()
        if u is None or not verify_password(password, u.password_hash):
            auth_log.warn(
                'login_failed', username=username,
                reason='unknown_user' if u is None else 'bad_password',
            )
            return None

        # Stamp last_login. Keep the ISO-8601 string shape the rest of
        # the code assumes.
        u.last_login = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        session.add(u)
        session.commit()

        token = create_token(u.id, u.username, u.role)
        auth_log.write('login_success', user_id=u.id, username=u.username, role=u.role)
        return {
            "token": token,
            "user": {
                "id": u.id,
                "username": u.username,
                "display_name": u.display_name,
                "role": u.role,
            },
        }
