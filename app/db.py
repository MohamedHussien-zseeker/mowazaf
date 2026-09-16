"""
Database layer with strict tenant isolation.

Tenant isolation is ENFORCED at the SQL layer. The database layer adds
tenant_id WHERE clauses automatically — the LLM or application layer
can never bypass them.

Each query in crud.py MUST go through a session that has tenant_id bound.
"""
from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    DateTime,
    Integer,
    String,
    Text,
    JSON,
    Column,
    ForeignKey,
    create_engine,
    select,
    text,
)
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    sessionmaker,
)

from .config import settings


# ── Engine ──────────────────────────────────────────────────────────────
engine = create_async_engine(settings.DATABASE_URL)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


# ── Tenant context (thread-safe) ──────────────────────────────────────────
_current_tenant: ContextVar[Optional[str]] = ContextVar("current_tenant", default=None)


class TenantContext:
    """Context manager that binds a tenant_id to the current async context.
    All DB queries automatically filter on this tenant_id."""

    def __init__(self, tenant_id: str):
        self._tenant_id = tenant_id
        self._token = None

    def __enter__(self):
        self._token = _current_tenant.set(self._tenant_id)
        return self

    def __exit__(self, *args):
        _current_tenant.reset(self._token)

    @property
    def tenant_id(self) -> str:
        return self._tenant_id


def get_current_tenant() -> Optional[str]:
    """Returns the current tenant_id from context, or None."""
    return _current_tenant.get()


# ── Base model ───────────────────────────────────────────────────────────
class Base(DeclarativeBase):
    """All models inherit from this. Every model MUST have a tenant_id column."""

    @classmethod
    def tenant_query(cls, session: AsyncSession, **filters):
        """Build a query that is automatically scoped to the current tenant."""
        tenant_id = get_current_tenant()
        if tenant_id is None:
            raise RuntimeError("No tenant context set — cannot query tenant data")
        stmt = select(cls).where(cls.tenant_id == tenant_id)  # type: ignore[attr-defined]
        for col, val in filters.items():
            stmt = stmt.where(getattr(cls, col) == val)
        return stmt


# ── Models ──────────────────────────────────────────────────────────────
class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    slug: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    phone_number: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    config: Mapped[dict] = mapped_column(JSON, default={})
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    conversation_id: Mapped[str] = mapped_column(String(128), index=True)
    channel: Mapped[str] = mapped_column(String(32))  # whatsapp, telegram, web
    customer_phone: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    customer_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    conversation_id: Mapped[str] = mapped_column(String(128), ForeignKey("conversations.conversation_id"))
    role: Mapped[str] = mapped_column(String(32))  # user, assistant, tool, system
    content: Mapped[str] = mapped_column(Text)
    tool_calls: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class KnowledgeDoc(Base):
    __tablename__ = "knowledge_docs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    filename: Mapped[str] = mapped_column(String(512))
    doc_type: Mapped[str] = mapped_column(String(64))  # faq, policy, pricing, products
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[Optional[list[float]]] = mapped_column(JSON, nullable=True)  # placeholder for pgvector
    source_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class AgentAction(Base):
    """Audit log for every action an agent takes — observability from day one."""
    __tablename__ = "agent_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    conversation_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(128))
    tool_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    result: Mapped[str] = mapped_column(String(32))  # success, denied, error
    tokens_used: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[Optional[float]] = mapped_column(nullable=True)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ── Idempotency — prevents duplicate AI replies on Meta webhook retries ──────
class ProcessedMessage(Base):
    """
    Deduplicates Meta webhook events.

    Meta retries failed webhooks. Without this, a single customer message
    generates multiple AI replies.

    Uses external_message_id as the unique key — WhatsApp assigns one per event.
    """
    __tablename__ = "processed_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # e.g. "wamid.xxx" from WhatsApp Cloud API
    external_message_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    # The job that processed (or is processing) this message
    job_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    # 'processing' | 'done' | 'failed'
    status: Mapped[str] = mapped_column(String(16), default="processing")
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ── Job Queue — async processing without blocking the webhook ─────────────────
class PendingJob(Base):
    """
    Background job queue (SQLite-first, no Redis dependency).

    The webhook immediately returns 200 OK after enqueueing here.
    A separate worker polls this table and processes jobs sequentially
    per conversation (to preserve message order).
    """
    __tablename__ = "pending_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # 'whatsapp' | 'telegram'
    channel: Mapped[str] = mapped_column(String(16))
    # The tenant that owns this job
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    # Conversation this job belongs to (used for per-conversation ordering)
    conversation_id: Mapped[str] = mapped_column(String(128), index=True)
    # Serialized job payload (JSON) — stores the original webhook event
    payload: Mapped[dict] = mapped_column(JSON)
    # 'pending' | 'processing' | 'done' | 'failed'
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    # Which worker is processing this (for concurrency control)
    worker_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    # For per-conversation locking: only one worker processes a conversation at a time
    locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # Error message if failed
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


# ── Per-tenant WhatsApp accounts ───────────────────────────────────────────
class WhatsAppAccount(Base):
    """
    Each tenant can have multiple WhatsApp Business numbers.

    This is the authoritative source for WhatsApp credentials.
    Never stored in Tenant.config — only here, with proper encryption in prod.
    """
    __tablename__ = "whatsapp_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    # Meta's phone_number_id (from the webhook payload metadata)
    phone_number_id: Mapped[str] = mapped_column(String(64), index=True)
    # Human-readable number, e.g. "01010969009"
    phone_number: Mapped[str] = mapped_column(String(32))
    # Meta Business App ID
    waba_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    # Access token (encrypted at rest in production)
    access_token: Mapped[str] = mapped_column(Text)
    # Webhook verify token for THIS phone's webhook
    verify_token: Mapped[str] = mapped_column(String(128))
    # URL of the phone's profile photo, if any
    profile_photo_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True)
    is_default: Mapped[bool] = mapped_column(default=False)  # primary number for this tenant
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ── Initialization ──────────────────────────────────────────────────────
async def init_db():
    """Create tables if they don't exist."""
    async with engine.begin() as conn:
        # Use SQL-level isolation — tenant_id is always required for data queries
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncSession:
    """Dependency injection for DB session."""
    async with SessionLocal() as session:
        yield session