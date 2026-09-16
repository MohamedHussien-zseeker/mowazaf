"""Auth API — login endpoint that issues a tenant-scoped JWT.

POST /api/auth/login
    {"username": "...", "password": "..."}
    -> {"access_token": "...", "token_type": "bearer"}

The returned JWT carries ``tenant_id`` so every subsequent request is
automatically routed to the correct tenant's data.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import create_access_token, verify_password
from database import get_session
from models import User

router = APIRouter()


class LoginIn(BaseModel):
    username: str
    password: str


@router.post("/login")
async def login(
    body: LoginIn,
    session: AsyncSession = Depends(get_session),
):
    """Authenticate a user and return a tenant-scoped JWT."""
    stmt = select(User).where(
        User.username == body.username,
        User.is_active.is_(True),
    )
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()

    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    token = create_access_token(
        data={"sub": str(user.id), "username": user.username, "role": user.role},
        tenant_id=user.tenant_id,
    )
    return {"access_token": token, "token_type": "bearer"}
