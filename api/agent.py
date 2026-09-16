"""Agent API — conversation management and direct agent interaction.

Used by the customer dashboard to view conversations and test the agent.

All endpoints require a valid JWT.  The ``tenant_id`` from the token is bound
to the DB context via ``TenantContext`` so every query is scoped at the SQL
level — a user can **never** read another tenant's conversations.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from auth import get_current_user
from crud import add_message, get_conversation, get_conversation_messages, get_conversations, log_action
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_session, TenantContext
from hermes_client import dispatch_to_agent
from models import Conversation

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
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """List conversations for the authenticated tenant."""
    async with TenantContext(user["tenant_id"]):
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
async def get_conversation_endpoint(
    conv_id: str,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """Get a conversation with all messages (tenant-scoped)."""
    async with TenantContext(user["tenant_id"]):
        conv = await get_conversation(session, conv_id)
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
                {"role": m.role, "content": m.content} for m in messages
            ],
        }


@router.post("/conversations/{conv_id}", response_model=MessageOut)
async def send_conversation_message(
    conv_id: str,
    body: MessageIn,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """Send a message to an existing conversation (routes to the agent)."""
    async with TenantContext(user["tenant_id"]):
        conv = await get_conversation(session, conv_id)
        if not conv:
            raise HTTPException(status_code=404, detail="Conversation not found")

        # Store the user's message
        await add_message(session, conv.conversation_id, "user", body.message)
        await session.commit()

        # Dispatch to Hermes agent (V1 stub)
        response_text, _ = await dispatch_to_agent(
            tenant_id=user["tenant_id"],
            conversation_id=conv.conversation_id,
            message=body.message,
            session=session,
        )

        await add_message(session, conv.conversation_id, "assistant", response_text)
        await log_action(
            session, conv.conversation_id, "agent_query", result="success"
        )
        await session.commit()

        return {"role": "assistant", "content": response_text}


@router.post("/conversations/test", response_model=MessageOut)
async def test_agent(
    body: MessageIn,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """Test the agent — creates an ephemeral test conversation (tenant-scoped)."""
    import secrets

    async with TenantContext(user["tenant_id"]):
        test_conv_id = f"test_{secrets.token_hex(4)}"

        from crud import get_or_create_conversation

        await get_or_create_conversation(
            session, conversation_id=test_conv_id, channel="test"
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
