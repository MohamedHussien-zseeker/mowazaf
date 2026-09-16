"""Authentication & Authorization.

JWT tokens carry ``tenant_id`` so the API layer can bind the tenant context
before any DB access.  Passwords are hashed with PBKDF2-HMAC-SHA256 (stdlib,
no native extensions).
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt

from config import settings

ALGORITHM = "HS256"
PBKDF2_ITERS = 200_000

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


# ── Password hashing (stdlib PBKDF2) ────────────────────────────────────────


def hash_password(password: str) -> str:
    """Return ``pbkdf2_sha256$iters$salt_hex$hash_hex``."""
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERS)
    return f"pbkdf2_sha256${PBKDF2_ITERS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, hashed: str) -> bool:
    """Constant-time password verification."""
    try:
        algo, iters_s, salt_hex, dk_hex = hashed.split("$")
        if algo != "pbkdf2_sha256":
            return False
        iters = int(iters_s)
        salt = bytes.fromhex(salt_hex)
        expected_dk = bytes.fromhex(dk_hex)
        actual_dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iters)
        return hmac.compare_digest(expected_dk, actual_dk)
    except (ValueError, AttributeError, TypeError):
        return False


# ── JWT ─────────────────────────────────────────────────────────────────────


def create_access_token(data: dict, tenant_id: str) -> str:
    """Create a JWT that embeds ``tenant_id`` so the caller is routed to the
    correct tenant's data."""
    to_encode = data.copy()
    to_encode["tenant_id"] = tenant_id
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode["exp"] = expire
    return jwt.encode(to_encode, settings.SECRET, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    """Decode + validate a JWT.  Raises 401 on any error."""
    try:
        return jwt.decode(token, settings.SECRET, algorithms=[ALGORITHM])
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


# ── FastAPI dependencies ───────────────────────────────────────────────────


def get_current_user(token: str = Depends(oauth2_scheme)) -> dict:
    """Validate the JWT and return a user dict with ``tenant_id``.

    The returned dict is consumed by every protected endpoint.  The
    ``tenant_id`` is used to bind the DB context so **no query can ever
    cross tenant boundaries**.
    """
    payload = decode_token(token)
    user_id = payload.get("sub")
    tenant_id = payload.get("tenant_id")
    role = payload.get("role", "viewer")

    if not user_id or not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing sub or tenant_id",
        )

    return {"user_id": user_id, "tenant_id": tenant_id, "role": role}


def require_role(*allowed_roles: str):
    """Dependency factory: reject callers whose role is not in ``allowed_roles``."""
    def role_checker(u: dict = Depends(get_current_user)) -> dict:
        if u["role"] not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{u['role']}' not allowed. Required: {allowed_roles}",
            )
        return u
    return role_checker
