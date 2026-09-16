"""
Agent API — conversation management and direct agent interaction.

Used by the customer dashboard to view conversations and test the agent.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user
from ..crud import (
    get_conversation_messages,
    get_conversations,
    add_message,
    log_action,
    get_or_create_conversation,
)
from ..db import TenantContext, Conversation
from ..hermes_client import dispatch_to_agent
from ..api.webhook import get_db_session

router = APIRouter()


class MessageIn(BaseModel):
    message: str


class MessageOut(BaseModel):
    role: str
    content: str


class ConversationOut(BaseModel):
    conversation_id: str
    channel: str
    customer_phone: Optional[str] = None
    customer_name: Optional[str] = None
    status: str
    created_at: str


class ConversationDetail(ConversationOut):
    messages: list[MessageOut]


@router.get("/conversations", response_model=list[ConversationOut])
async def list_conversations(
    session: AsyncSession = Depends(get_db_session),
    user: dict = Depends(get_current_user),
):
    """List conversations for the current tenant."""
    with TenantContext(user["tenant_id"]):
        convs = await get_conversations(session)
        return [
            {
                "conversation_id": c.conversation_id,
                "channel": c.channel,
                "customer_phone": c.customer_phone,
                "customer_name": c.customer_name,
                "status": c.status,
                "created_at": c.created_at.isoformat(),
            }
            for c in convs
        ]


@router.get("/conversations/{conv_id}", response_model=ConversationDetail)
async def get_conversation(
    conv_id: str,
    session: AsyncSession = Depends(get_db_session),
    user: dict = Depends(get_current_user),
):
    """Get a conversation with all messages."""
    with TenantContext(user["tenant_id"]):
        stmt = select(Conversation).where(Conversation.conversation_id == conv_id)
        result = await session.execute(stmt)
        conv = result.scalar_one_or_none()
        if not conv:
            raise HTTPException(status_code=404, detail="Conversation not found")

        messages = await get_conversation_messages(session, conv_id)
        return {
            "conversation_id": conv.conversation_id,
            "channel": conv.channel,
            "customer_phone": conv.customer_phone,
            "customer_name": conv.customer_name,
            "status": conv.status,
            "created_at": conv.created_at.isoformat(),
            "messages": [
                {"role": m.role, "content": m.content}
                for m in messages
            ],
        }


@router.post("/conversations/test", response_model=MessageOut)
async def test_agent(
    body: MessageIn,
    session: AsyncSession = Depends(get_db_session),
    user: dict = Depends(get_current_user),
):
    """Test the agent — for the customer's 'Test Agent' dashboard feature."""
    with TenantContext(user["tenant_id"]):
        import secrets
        test_conv_id = f"test_{secrets.token_hex(4)}"

        await get_or_create_conversation(
            session,
            conversation_id=test_conv_id,
            channel="test",
        )

        await add_message(session, test_conv_id, "user", body.message)
        await session.commit()

        response_text, _ = await dispatch_to_agent(
            tenant_id=user["tenant_id"],
            conversation_id=test_conv_id,
            message=body.message,
            session=session,
        )

        await add_message(session, test_conv_id, "assistant", response_text)
        await log_action(session, test_conv_id, "agent_test_query", result="success")
        await session.commit()

        return {"role": "assistant", "content": response_text}