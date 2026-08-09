from __future__ import annotations

import hashlib
import hmac
import secrets
from fastapi import Header, HTTPException, status

from .config import settings

PBKDF2_ITERATIONS = 310_000


def generate_secret(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def hash_secret(secret: str, salt_hex: str | None = None) -> tuple[str, str]:
    salt = bytes.fromhex(salt_hex) if salt_hex else secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", secret.encode("utf-8"), salt, PBKDF2_ITERATIONS
    )
    return salt.hex(), digest.hex()


def verify_secret(secret: str, salt_hex: str, digest_hex: str) -> bool:
    _, candidate = hash_secret(secret, salt_hex)
    return hmac.compare_digest(candidate, digest_hex)


def require_admin(authorization: str | None = Header(default=None)) -> None:
    expected = settings.admin_token
    if not expected or expected.startswith("CHANGE_ME"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin token is not configured.",
        )
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing admin bearer token.")
    supplied = authorization[7:]
    if not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=403, detail="Invalid admin token.")
