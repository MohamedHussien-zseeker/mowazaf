"""
CRUD operations — ALL queries are scoped to the current tenant_id.

The DB layer enforces tenant isolation. No query can cross tenant boundaries.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import insert, select, update, delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from .db import TenantContext, Conversation, KnowledgeDoc, Message, AgentAction, get_current_tenant


# ── Tenants ─────────────────────────────────────────────────────────────

async def get_tenant_by_phone(session: AsyncSession, phone: str) -> Optional[Tenant]:
    """Look up a tenant by their WhatsApp Business phone number_id.
    This is how the WhatsApp webhook routes incoming messages to the right tenant."""
    from .db import Tenant
    stmt = select(Tenant).where(Tenant.phone_number == phone)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_tenant_by_slug(session: AsyncSession, slug: str) -> Optional[Tenant]:
    from .db import Tenant
    stmt = select(Tenant).where(Tenant.slug == slug)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_tenant_by_telegram_token(session: AsyncSession, bot_chat_id: str) -> Optional[Tenant]:
    """
    Look up a tenant by their Telegram bot's numeric chat_id.

    The mapping is stored in tenant.config as:
        telegram_bot_token: "<bot_token>"
        telegram_bot_chat_id: "<numeric_chat_id>"

    We look for a tenant whose config has a matching telegram_bot_chat_id.
    """
    from .db import Tenant
    stmt = select(Tenant).where(Tenant.is_active == True)
    result = await session.execute(stmt)
    for tenant in result.scalars().all():
        cfg = tenant.config or {}
        if cfg.get("telegram_bot_chat_id") == bot_chat_id:
            return tenant
    return None


# ── Conversations ────────────────────────────────────────────────────

async def get_or_create_conversation(
    session: AsyncSession,
    conversation_id: str,
    channel: str,
    customer_phone: Optional[str] = None,
    customer_name: Optional[str] = None,
) -> Conversation:
    """Get an existing conversation or create one (tenant-scoped).

    NOTE: Conversations are tenant-scoped. We MUST set `tenant_id` and we
    MUST query by `(tenant_id, conversation_id)`.
    """
    tid = get_current_tenant()
    if tid is None:
        raise RuntimeError("No tenant context set")

    stmt = select(Conversation).where(
        Conversation.tenant_id == tid,
        Conversation.conversation_id == conversation_id,
    )
    result = await session.execute(stmt)
    conv = result.scalar_one_or_none()

    if conv is None:
        conv = Conversation(
            tenant_id=tid,
            conversation_id=conversation_id,
            channel=channel,
            customer_phone=customer_phone,
            customer_name=customer_name,
        )
        session.add(conv)
        await session.flush()

    return conv


async def get_conversations(
    session: AsyncSession,
    limit: int = 50,
    status: Optional[str] = None,
) -> list[Conversation]:
    stmt = select(Conversation).order_by(Conversation.created_at.desc()).limit(limit)
    if status:
        stmt = stmt.where(Conversation.status == status)
    result = await session.execute(stmt)
    return list(result.scalars().all())


# ── Messages ──────────────────────────────────────────────────────────

async def add_message(
    session: AsyncSession,
    conversation_id: str,
    role: str,
    content: str,
    tool_calls: Optional[dict] = None,
) -> Message:
    tid = get_current_tenant()
    if tid is None:
        raise RuntimeError("No tenant context set")
    msg = Message(
        tenant_id=tid,
        conversation_id=conversation_id,
        role=role,
        content=content,
        tool_calls=tool_calls,
    )
    session.add(msg)
    await session.flush()
    return msg


async def get_conversation_messages(session: AsyncSession, conversation_id: str) -> list[Message]:
    stmt = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


# ── Knowledge ─────────────────────────────────────────────────────────

async def add_knowledge_doc(
    session: AsyncSession,
    filename: str,
    doc_type: str,
    content: str,
    source_url: Optional[str] = None,
) -> KnowledgeDoc:
    doc = KnowledgeDoc(
        filename=filename,
        doc_type=doc_type,
        content=content,
        source_url=source_url,
    )
    session.add(doc)
    await session.flush()
    return doc


async def list_knowledge_docs(
    session: AsyncSession,
    doc_type: Optional[str] = None,
) -> list[KnowledgeDoc]:
    stmt = select(KnowledgeDoc).order_by(KnowledgeDoc.created_at.desc())
    if doc_type:
        stmt = stmt.where(KnowledgeDoc.doc_type == doc_type)
    result = await session.execute(stmt)
    return list(result.scalars().all())


# ── Action Logging ───────────────────────────────────────────────────

async def log_action(
    session: AsyncSession,
    conversation_id: Optional[str],
    action: str,
    tool_name: Optional[str] = None,
    result: str = "success",
    tokens_used: Optional[int] = None,
    cost_usd: Optional[float] = None,
    latency_ms: Optional[int] = None,
) -> AgentAction:
    """Log every agent action for observability — tenant_id is injected from context."""
    tid = get_current_tenant()
    if tid is None:
        raise RuntimeError("No tenant context set")
    entry = AgentAction(
        tenant_id=tid,
        conversation_id=conversation_id,
        action=action,
        tool_name=tool_name,
        result=result,
        tokens_used=tokens_used,
        cost_usd=cost_usd,
        latency_ms=latency_ms,
    )
    session.add(entry)
    await session.flush()
    return entry


# ── Tenant context helper ────────────────────────────────────────────

async def tenant_session(session: AsyncSession, tenant_id: str) -> AsyncSession:
    """
    Wraps a session in tenant context.

    Usage:
        async with TenantContext(tenant_id):
            result = await crud.get_conversations(session)
    """
    # This is called by the dependency; actual filtering happens via TenantContext
    return session