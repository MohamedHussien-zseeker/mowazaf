"""SQLAlchemy models — every tenant-scoped table has a ``tenant_id`` column.

The column is indexed and is the single axis of isolation: every query in
``crud.py`` is filtered on ``tenant_id`` (enforced via ``Base._scoped`` and the
``_guard()`` check in database.py).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, JSON, String, Text, Integer, Column, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from database import Base, _utcnow

# ── Users ───────────────────────────────────────────────────────────────────


class User(Base):
    """A user account belonging to a tenant — used for JWT login.

    ``tenant_id`` is populated from the JWT on every authenticated request,
    ensuring users can only touch their own data.
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    full_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    role: Mapped[str] = mapped_column(String(32), default="viewer")  # admin | manager | agent | viewer
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


# ── Tenants ────────────────────────────────────────────────────────────────


class Tenant(Base):
    """A business account on the Mowazaf platform.

    ``phone_number_id`` is the WhatsApp Business *phone_number_id* from the
    Meta webhook payload — it is the keying field that routes an inbound
    WhatsApp message to the correct tenant.
    """

    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Public identifier embedded in JWTs and used for scoping
    tenant_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    slug: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    # WhatsApp Business phone_number_id (Meta) — routes webhooks to this tenant
    phone_number_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    # Human-readable phone number (for display / debugging)
    phone_number: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    # Per-tenant agent config (personality, language, model, etc.)
    config: Mapped[dict] = mapped_column(JSON, default={})
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


# ── Conversations ──────────────────────────────────────────────────────────


class Conversation(Base):
    """A single thread between a customer and the tenant's agent.

    ``conversation_id`` is a human-readable slug (e.g. ``wa_15551234567``)
    used in URLs and as the routing key to the Hermes agent.
    """

    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    conversation_id: Mapped[str] = mapped_column(String(128, ), unique=True, index=True)
    channel: Mapped[str] = mapped_column(String(32))  # whatsapp | telegram | web | test
    customer_phone: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    customer_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="open")  # open | closed
    hermes_conversation_ref: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


# ── Messages ───────────────────────────────────────────────────────────────


class Message(Base):
    """A single message within a conversation (user, assistant, system, tool)."""

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.conversation_id"), index=True)
    role: Mapped[str] = mapped_column(String(32))  # user | assistant | system | tool
    content: Mapped[str] = mapped_column(Text)
    tool_calls: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


# ── Knowledge ──────────────────────────────────────────────────────────────


class KnowledgeDoc(Base):
    """Per-tenant knowledge document (FAQ, policy, product catalog, …)."""

    __tablename__ = "knowledge_docs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    filename: Mapped[str] = mapped_column(String(512))
    doc_type: Mapped[str] = mapped_column(String(64))  # faq | policy | pricing | products
    content: Mapped[str] = mapped_column(Text)
    # Placeholder — populated via pgvector / OpenAI embeddings in Phase 2
    embedding: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    source_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


# ── Agent audit ────────────────────────────────────────────────────────────


class AgentAction(Base):
    """Audit log for every action the Hermes agent takes."""

    __tablename__ = "agent_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    conversation_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(128))
    tool_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    result: Mapped[str] = mapped_column(String(32))  # success | denied | error
    tokens_used: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[Optional[float]] = mapped_column(nullable=True)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
