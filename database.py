"""Database layer with **strict tenant isolation enforced at the SQL level**.

Every query against tenant-scoped tables MUST go through a session that has a
tenant_id bound via ``TenantContext``.  The ``_scoped`` helper injects
``tenant_id == <current>`` into every SELECT/UPDATE/DELETE so that no row from
another tenant can ever be read or written — not by accident and not by the LLM.

How it works
------------
1. ``Base`` declares the shared ``tenant_id`` column and a ``_scoped()`` class
   method that returns a SELECT pre-filtered by the current tenant.
2. ``TenantContext`` is an async context manager that sets a ``ContextVar``
   holding the active tenant_id.
3. ``get_current_tenant()`` reads that ContextVar.  If it is ``None`` the
   ``_scoped()`` guard raises — queries without an active tenant are rejected.
4. In webhook routes the tenant is resolved from the WhatsApp ``phone_number_id``
   and the context is set explicitly.  In authenticated routes the tenant comes
   from the JWT payload (validated in ``auth.get_current_user``).
"""
from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, JSON, String, Text, Integer, Column
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from config import settings

# ── Engine / Session ────────────────────────────────────────────────────────

engine = create_async_engine(settings.DATABASE_URL, echo=False, future=True)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


# ── Tenant context (async-context-safe) ─────────────────────────────────────

_current_tenant: ContextVar[Optional[str]] = ContextVar("current_tenant", default=None)


class TenantContext:
    """Bind ``tenant_id`` to the current async context.

    All DB queries issued while the context is active are automatically
    scoped to this tenant at the SQL level.
    """

    __slots__ = ("_tenant_id", "_token")

    def __init__(self, tenant_id: str):
        self._tenant_id = tenant_id
        self._token: Optional[object] = None

    async def __aenter__(self) -> "TenantContext":
        self._token = _current_tenant.set(self._tenant_id)
        return self

    async def __aexit__(self, *exc):
        if self._token is not None:
            _current_tenant.reset(self._token)
            self._token = None

    # synchronous context manager support (for non-async callers)
    def __enter__(self) -> "TenantContext":
        self._token = _current_tenant.set(self._tenant_id)
        return self

    def __exit__(self, *exc):
        if self._token is not None:
            _current_tenant.reset(self._token)
            self._token = None

    @property
    def tenant_id(self) -> str:
        return self._tenant_id


def get_current_tenant() -> Optional[str]:
    """Return the tenant_id bound to the current context, or None."""
    return _current_tenant.get()


def _guard() -> str:
    """Raise if no tenant context is active — never query without one."""
    tid = get_current_tenant()
    if tid is None:
        raise RuntimeError(
            "No tenant context — every DB query must be scoped to a tenant_id. "
            "Wrap the call in `async with TenantContext(tenant_id):`."
        )
    return tid


# ── Base ────────────────────────────────────────────────────────────────────


class Base(DeclarativeBase):
    """All models inherit from this.

    Every model that holds tenant-scoped data MUST include a ``tenant_id``
    ``Mapped[str]`` column (declared in the individual model class).
    """

    @classmethod
    def _scoped(cls, stmt):
        """Inject the current tenant_id into a SELECT — the enforcement hook.

        Usage in crud.py:
            stmt = Tenant._scoped(select(Tenant).where(Tenant.tenant_id == tid))
        """
        tid = _guard()
        return stmt.where(cls.tenant_id == tid)  # type: ignore[attr-defined]


# ── Shared column helpers ───────────────────────────────────────────────────


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── Session dependency ──────────────────────────────────────────────────────


async def get_session() -> AsyncSession:
    """FastAPI dependency that yields an ``AsyncSession``."""
    async with SessionLocal() as session:
        yield session


# ── Init ────────────────────────────────────────────────────────────────────


async def init_db() -> None:
    """Create all tables (idempotent)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
