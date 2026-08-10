from __future__ import annotations

import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import Cookie, Header, HTTPException, status

from .config import settings
from .identity_db import identity_db
from .security import hash_secret, verify_secret

SESSION_HOURS = 12
USERNAME_RE = re.compile(r"^[a-z][a-z0-9._-]{1,31}$")
MIN_PASSWORD_LENGTH = 10


@dataclass(frozen=True)
class Operator:
    username: str
    display_name: str
    role: str
    csrf_token: str


def _normalized_username(username: str) -> str:
    value = username.strip().lower()
    if not USERNAME_RE.fullmatch(value):
        raise ValueError("Username must be 2-32 characters: lowercase letters, digits, dot, underscore or hyphen")
    return value


def _validate_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must contain at least {MIN_PASSWORD_LENGTH} characters")


def user_count() -> int:
    with identity_db() as conn:
        return int(conn.execute("SELECT COUNT(*) AS n FROM admin_users").fetchone()["n"])


def setup_status(local_request: bool = False) -> dict:
    initialized = user_count() > 0
    local_mode = local_request
    return {
        "initialized": initialized,
        "setup_token_required": (not initialized) and (not local_mode),
    }


def create_admin_user(username: str, password: str, role: str = "ra", display_name: str = "") -> None:
    username = _normalized_username(username)
    if role not in {"pi", "ra"}:
        raise ValueError("Invalid role")
    _validate_password(password)
    salt, digest = hash_secret(password)
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    with identity_db() as conn:
        conn.execute(
            "INSERT INTO admin_users(username,display_name,role,password_salt,password_hash,is_active,created_at_utc) VALUES(?,?,?,?,?,?,?)",
            (username, display_name.strip(), role, salt, digest, 1, now),
        )


def bootstrap_first_pi(username: str, password: str, display_name: str = "", setup_token: str = "", local_request: bool = False):
    """Create the first PI account exactly once.

    Local development does not require a setup token. Public deployments require
    the existing ADMIN_TOKEN from .env as a one-time bootstrap secret. Once any
    admin user exists this function is permanently closed.
    """
    username = _normalized_username(username)
    _validate_password(password)
    local_mode = local_request
    if not local_mode:
        expected = settings.admin_token
        if not expected or expected == "CHANGE_ME_TO_A_LONG_RANDOM_ADMIN_TOKEN":
            raise ValueError("Server setup token is not configured")
        if not setup_token or not hmac.compare_digest(setup_token, expected):
            raise PermissionError("Invalid setup token")

    salt, digest = hash_secret(password)
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    with identity_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT 1 FROM admin_users LIMIT 1").fetchone():
            raise RuntimeError("LEHUE has already been initialized")
        conn.execute(
            "INSERT INTO admin_users(username,display_name,role,password_salt,password_hash,is_active,created_at_utc) VALUES(?,?,?,?,?,?,?)",
            (username, display_name.strip(), "pi", salt, digest, 1, now),
        )
    return authenticate_admin(username, password)


def authenticate_admin(username: str, password: str):
    with identity_db() as conn:
        row = conn.execute(
            "SELECT * FROM admin_users WHERE username=?", (username.strip().lower(),)
        ).fetchone()
    if not row or not row["is_active"]:
        return None
    if not verify_secret(password, row["password_salt"], row["password_hash"]):
        return None
    return row


def list_admin_users() -> list[dict]:
    with identity_db() as conn:
        rows = conn.execute(
            "SELECT username,display_name,role,is_active,created_at_utc FROM admin_users ORDER BY role, username"
        ).fetchall()
    return [dict(r) for r in rows]


def set_admin_active(username: str, is_active: bool, operator_username: str) -> None:
    username = username.strip().lower()
    if username == operator_username:
        raise ValueError("You cannot disable your own account")
    with identity_db() as conn:
        row = conn.execute("SELECT role,is_active FROM admin_users WHERE username=?", (username,)).fetchone()
        if not row:
            raise ValueError("User not found")
        if row["role"] == "pi" and row["is_active"] and not is_active:
            active_pi = conn.execute("SELECT COUNT(*) n FROM admin_users WHERE role='pi' AND is_active=1").fetchone()["n"]
            if active_pi <= 1:
                raise ValueError("The last active PI account cannot be disabled")
        conn.execute("UPDATE admin_users SET is_active=? WHERE username=?", (1 if is_active else 0, username))
        if not is_active:
            conn.execute("DELETE FROM web_sessions WHERE username=?", (username,))


def reset_admin_password(username: str, password: str) -> None:
    username = username.strip().lower()
    _validate_password(password)
    salt, digest = hash_secret(password)
    with identity_db() as conn:
        if not conn.execute("SELECT 1 FROM admin_users WHERE username=?", (username,)).fetchone():
            raise ValueError("User not found")
        conn.execute(
            "UPDATE admin_users SET password_salt=?,password_hash=? WHERE username=?",
            (salt, digest, username),
        )
        conn.execute("DELETE FROM web_sessions WHERE username=?", (username,))


def new_session(username: str) -> tuple[str, str]:
    sid = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(24)
    now = datetime.now(timezone.utc)
    expires = now + timedelta(hours=SESSION_HOURS)
    with identity_db() as conn:
        conn.execute(
            "INSERT INTO web_sessions(session_id,username,csrf_token,expires_at_utc,created_at_utc) VALUES(?,?,?,?,?)",
            (
                sid,
                username,
                csrf,
                expires.isoformat().replace("+00:00", "Z"),
                now.isoformat().replace("+00:00", "Z"),
            ),
        )
    return sid, csrf


def delete_session(session_id: str | None) -> None:
    if not session_id:
        return
    with identity_db() as conn:
        conn.execute("DELETE FROM web_sessions WHERE session_id=?", (session_id,))


def _load_operator(session_id: str | None) -> Operator:
    if not session_id:
        raise HTTPException(status_code=401, detail="Login required")
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    with identity_db() as conn:
        row = conn.execute(
            """
            SELECT s.csrf_token,u.username,u.display_name,u.role
            FROM web_sessions s JOIN admin_users u ON u.username=s.username
            WHERE s.session_id=? AND s.expires_at_utc>? AND u.is_active=1
            """,
            (session_id, now),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="Session expired")
    return Operator(row["username"], row["display_name"], row["role"], row["csrf_token"])


def require_operator(lehue_session: str | None = Cookie(default=None)) -> Operator:
    return _load_operator(lehue_session)


def require_operator_write(
    lehue_session: str | None = Cookie(default=None),
    x_csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
) -> Operator:
    operator = _load_operator(lehue_session)
    if not x_csrf_token or not hmac.compare_digest(x_csrf_token, operator.csrf_token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token")
    return operator


def require_pi(lehue_session: str | None = Cookie(default=None)) -> Operator:
    resolved = _load_operator(lehue_session)
    if resolved.role != "pi":
        raise HTTPException(status_code=403, detail="PI permission required")
    return resolved


def require_pi_write(
    lehue_session: str | None = Cookie(default=None),
    x_csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
) -> Operator:
    operator = _load_operator(lehue_session)
    if operator.role != "pi":
        raise HTTPException(status_code=403, detail="PI permission required")
    if not x_csrf_token or not hmac.compare_digest(x_csrf_token, operator.csrf_token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token")
    return operator
