"""
Tenant management API.

Internal admin endpoints for creating and managing tenants.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_role
from ..db import SessionLocal, Tenant

router = APIRouter()


class TenantCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    slug: str = Field(..., min_length=3, max_length=128, pattern=r"^[a-z0-9-]+$")
    phone_number: Optional[str] = Field(None, max_length=32)
    config: dict = {}


class TenantOut(BaseModel):
    id: int
    tenant_id: str
    name: str
    slug: str
    phone_number: Optional[str] = None
    config: dict = {}
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


@router.post("/", response_model=TenantOut, status_code=status.HTTP_201_CREATED)
async def create_tenant(
    body: TenantCreate,
    session: AsyncSession = Depends(SessionLocal),
    admin: dict = Depends(require_role("admin")),
):
    """Create a new tenant (admin-only)."""
    # Check slug uniqueness
    stmt = select(Tenant).where(Tenant.slug == body.slug)
    result = await session.execute(stmt)
    if result.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Slug already exists")

    # Generate tenant_id
    import secrets
    tenant_id = f"tenant_{secrets.token_hex(6)}"

    tenant = Tenant(
        tenant_id=tenant_id,
        name=body.name,
        slug=body.slug,
        phone_number=body.phone_number,
        config=body.config,
    )
    session.add(tenant)
    await session.commit()
    await session.refresh(tenant)

    return tenant


@router.get("/", response_model=list[TenantOut])
async def list_tenants(
    session: AsyncSession = Depends(SessionLocal),
    admin: dict = Depends(require_role("admin")),
):
    stmt = select(Tenant).order_by(Tenant.created_at.desc())
    result = await session.execute(stmt)
    return list(result.scalars().all())


@router.get("/{tenant_id}", response_model=TenantOut)
async def get_tenant(
    tenant_id: str,
    session: AsyncSession = Depends(SessionLocal),
    admin: dict = Depends(require_role("admin")),
):
    stmt = select(Tenant).where(Tenant.tenant_id == tenant_id)
    result = await session.execute(stmt)
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return tenant


@router.patch("/{tenant_id}", response_model=TenantOut)
async def update_tenant(
    tenant_id: str,
    body: TenantCreate,
    session: AsyncSession = Depends(SessionLocal),
    admin: dict = Depends(require_role("admin")),
):
    stmt = select(Tenant).where(Tenant.tenant_id == tenant_id)
    result = await session.execute(stmt)
    tenant = result.scalar_one()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    tenant.name = body.name
    tenant.slug = body.slug
    tenant.phone_number = body.phone_number
    tenant.config = body.config

    await session.commit()
    await session.refresh(tenant)
    return tenant


@router.post("/{tenant_id}/deactivate", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_tenant(
    tenant_id: str,
    session: AsyncSession = Depends(SessionLocal),
    admin: dict = Depends(require_role("admin")),
):
    stmt = select(Tenant).where(Tenant.tenant_id == tenant_id)
    result = await session.execute(stmt)
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    tenant.is_active = False
    await session.commit()
    return None