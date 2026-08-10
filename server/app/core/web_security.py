from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import Cookie, Header, HTTPException, status

from .identity_db import identity_db
from .security import hash_secret, verify_secret

SESSION_HOURS = 12


@dataclass(frozen=True)
class Operator:
    username: str
    display_name: str
    role: str
    csrf_token: str


def create_admin_user(username: str, password: str, role: str = "ra", display_name: str = "") -> None:
    username = username.strip().lower()
    if not username or role not in {"pi", "ra"}:
        raise ValueError("Invalid username or role")
    salt, digest = hash_secret(password)
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    with identity_db() as conn:
        conn.execute(
            "INSERT INTO admin_users(username,display_name,role,password_salt,password_hash,is_active,created_at_utc) VALUES(?,?,?,?,?,?,?)",
            (username, display_name.strip(), role, salt, digest, 1, now),
        )


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
