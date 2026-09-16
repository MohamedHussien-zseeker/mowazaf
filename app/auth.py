"""
Authentication & Authorization.

JWT tokens include tenant_id to enforce tenant context at the API layer.
RBAC is role-based: admin, manager, agent.
"""
from __future__ import annotations

import os
import hmac
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt

from .config import settings
from .db import get_current_tenant

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 hours
PBKDF2_ITERS = 200_000

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def hash_password(password: str) -> str:
    """Hash a password using PBKDF2-HMAC-SHA256."""
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERS)
    return f"pbkdf2_sha256${PBKDF2_ITERS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, hashed: str) -> bool:
    """Verify a password against its hash using constant-time comparison."""
    try:
        algo, iters, salt_hex, dk_hex = hashed.split("$")
        iters = int(iters)
        salt = bytes.fromhex(salt_hex)
        expected_dk = bytes.fromhex(dk_hex)
        actual_dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iters)
        return hmac.compare_digest(expected_dk, actual_dk)
    except (ValueError, AttributeError):
        return False


def create_access_token(data: dict, tenant_id: str) -> str:
    """Create a JWT token with tenant_id embedded."""
    to_encode = data.copy()
    to_encode["tenant_id"] = tenant_id
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode["exp"] = expire
    return jwt.encode(to_encode, settings.SECRET, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    """Decode and validate a JWT token."""
    try:
        return jwt.decode(token, settings.SECRET, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )


def get_current_user(token: str = Depends(oauth2_scheme)) -> dict:
    """Extract and validate the current user from JWT.
    Returns user dict with tenant_id, role, and user_id."""
    payload = decode_token(token)
    user_id = payload.get("sub")
    tenant_id = payload.get("tenant_id")
    role = payload.get("role", "viewer")

    if not user_id or not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
        )

    return {"user_id": user_id, "tenant_id": tenant_id, "role": role}


def require_role(*allowed_roles: str):
    """Dependency that requires one of the allowed roles."""
    def role_checker(u: dict = Depends(get_current_user)):
        if u["role"] not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{u['role']}' not allowed. Required: {allowed_roles}",
            )
        return u
    return role_checker