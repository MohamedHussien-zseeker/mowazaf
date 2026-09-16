"""CRUD operations — **every tenant-scoped query is filtered by ``tenant_id``**.

Enforcement strategy
--------------------
* Functions that operate on tenant-scoped tables call ``_guard()`` at the top,
  which raises ``RuntimeError`` if no ``TenantContext`` is active.
* Each ``select()`` / ``update()`` / ``delete()`` includes
  ``cls.tenant_id == current_tenant`` as a WHERE clause.
* The webhook resolver (``get_tenant_by_phone_number_id``) reads from the
  shared ``tenants`` table *without* a tenant scope — this is the one place
  where the tenant is *discovered*, not queried.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import Base, _guard
from models import AgentAction, Conversation, KnowledgeDoc, Message, Tenant


# ── Tenant lookup (discovery — NOT scoped, this is how we find the tenant) ──


async def get_tenant_by_phone_number_id(
    session: AsyncSession, phone_number_id: str
) -> Optional[Tenant]:
    """Look up a tenant by a WhatsApp ``phone_number_id``.

    This is the webhook's routing key — it runs *before* ``TenantContext``
    is established and therefore queries the shared ``tenants`` table directly.
    """
    stmt = select(Tenant).where(Tenant.phone_number_id == phone_number_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_tenant_by_tid(session: AsyncSession, tenant_id: str) -> Optional[Tenant]:
    """Look up a tenant by its public ``tenant_id`` string."""
    stmt = select(Tenant).where(Tenant.tenant_id == tenant_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


# ── Conversations ──────────────────────────────────────────────────────────


async def get_or_create_conversation(
    session: AsyncSession,
    conversation_id: str,
    channel: str,
    customer_phone: Optional[str] = None,
    customer_name: Optional[str] = None,
) -> Conversation:
    """Get an existing conversation or create one.  Always tenant-scoped."""
    tid = _guard()  # ← fails if no TenantContext
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
    """List conversations for the current tenant."""
    tid = _guard()
    stmt = select(Conversation).where(Conversation.tenant_id == tid)
    if status:
        stmt = stmt.where(Conversation.status == status)
    stmt = stmt.order_by(Conversation.created_at.desc()).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_conversation(
    session: AsyncSession, conversation_id: str
) -> Optional[Conversation]:
    """Fetch a single conversation — scoped to the current tenant."""
    tid = _guard()
    stmt = select(Conversation).where(
        Conversation.tenant_id == tid,
        Conversation.conversation_id == conversation_id,
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


# ── Messages ───────────────────────────────────────────────────────────────


async def add_message(
    session: AsyncSession,
    conversation_id: str,
    role: str,
    content: str,
    tool_calls: Optional[dict] = None,
) -> Message:
    """Append a message to a conversation — scoped via the conversation's
    tenant_id (the conversation itself is tenant-scoped)."""
    tid = _guard()
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


async def get_conversation_messages(
    session: AsyncSession, conversation_id: str
) -> list[Message]:
    """All messages for a conversation — scoped to the current tenant."""
    tid = _guard()
    stmt = (
        select(Message)
        .where(
            Message.tenant_id == tid,
            Message.conversation_id == conversation_id,
        )
        .order_by(Message.created_at.asc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


# ── Knowledge ──────────────────────────────────────────────────────────────


async def add_knowledge_doc(
    session: AsyncSession,
    filename: str,
    doc_type: str,
    content: str,
    source_url: Optional[str] = None,
) -> KnowledgeDoc:
    tid = _guard()
    doc = KnowledgeDoc(
        tenant_id=tid,
        filename=filename,
        doc_type=doc_type,
        content=content,
        source_url=source_url,
    )
    session.add(doc)
    await session.flush()
    return doc


async def list_knowledge_docs(
    session: AsyncSession, doc_type: Optional[str] = None
) -> list[KnowledgeDoc]:
    tid = _guard()
    stmt = select(KnowledgeDoc).where(KnowledgeDoc.tenant_id == tid)
    if doc_type:
        stmt = stmt.where(KnowledgeDoc.doc_type == doc_type)
    stmt = stmt.order_by(KnowledgeDoc.created_at.desc())
    result = await session.execute(stmt)
    return list(result.scalars().all())


# ── Agent action logging ───────────────────────────────────────────────────


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
    """Log an agent action — tenant_id is injected from the active context."""
    tid = _guard()
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
