"""WhatsApp webhook endpoint.

Receives inbound messages from the WhatsApp Cloud API, extracts
``phone_number_id`` to route to the correct tenant, and forwards the message
to the Hermes agent (stub in V1).

Tenant isolation
----------------
The webhook is **unauthenticated** (Meta doesn't sign POSTs with our auth
tokens).  Instead, the tenant is resolved from the
``phone_number_id`` field in the payload.  Once the tenant is found,
``TenantContext`` is established and every subsequent DB write is scoped to
that tenant at the SQL level.
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from crud import (
    add_message,
    get_tenant_by_phone_number_id,
    get_or_create_conversation,
    log_action,
)
from database import get_session, TenantContext
from hermes_client import dispatch_to_agent

router = APIRouter()

# ── WhatsApp payload parsing helpers ─────────────────────────────────────


def _extract_payload(payload: dict) -> tuple[str | None, str | None, str | None]:
    """Extract ``phone_number_id``, ``from_number``, and ``text`` from a
    WhatsApp Cloud API webhook payload.

    Returns ``(phone_number_id, from_number, text_body)`` — any of which may be
    ``None`` if the payload doesn't contain a message (e.g. a status update).
    """
    entry = payload.get("entry", [])
    if not entry:
        return None, None, None

    changes = entry[0].get("changes", [])
    if not changes:
        return None, None, None

    value = changes[0].get("value", {})
    metadata = value.get("metadata", {})
    phone_number_id = metadata.get("phone_number_id")

    messages = value.get("messages", [])
    if not messages:
        return phone_number_id, None, None

    msg = messages[0]
    from_number = msg.get("from", "")
    msg_type = msg.get("type", "text")

    if msg_type == "text":
        text_body = msg.get("text", {}).get("body", "")
    else:
        # Non-text messages: store the raw payload as the message body
        text_body = json.dumps(msg.get(msg_type, {}))

    return phone_number_id, from_number, text_body


async def send_whatsapp_message(phone_number_id: str, to: str, text: str):
    """Send a reply via the WhatsApp Cloud API (no-op when WHATSAPP_TOKEN unset)."""
    import httpx

    if not settings.WHATSAPP_TOKEN:
        return  # No token configured — skip for local dev

    url = f"https://graph.facebook.com/v19.0/{phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {settings.WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }
    payload_req = {
        "messaging_product": "whatsapp",
        "to": to,
        "text": {"body": text},
    }
    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(url, headers=headers, json=payload_req)


# ── Routes ─────────────────────────────────────────────────────────────────


@router.get("/whatsapp")
async def verify_whatsapp_webhook(
    hub_mode: str = "",
    hub_verify_token: str = "",
    hub_challenge: str = "",
):
    """WhatsApp webhook verification handshake.

    Meta sends ``hub.verify_token`` which must match
    ``MOWAZAF_WHATSAPP_VERIFY_TOKEN``.
    """
    if (
        hub_mode == "subscribe"
        and hub_verify_token == settings.WHATSAPP_VERIFY_TOKEN
    ):
        return int(hub_challenge)
    raise HTTPException(status_code=403, detail="Verification failed")


@router.post("/whatsapp")
async def receive_whatsapp_message(
    req: Request,
    session: AsyncSession = Depends(get_session),
):
    """Receive an inbound WhatsApp message and route to the tenant's agent.

    Flow:
    1. Parse the WhatsApp payload to extract ``phone_number_id``.
    2. Resolve the tenant by ``phone_number_id`` (the only un-scoped query).
    3. Enter ``TenantContext(tenant.tenant_id)`` — all DB ops are now scoped.
    4. Store the inbound message + dispatch to the Hermes agent (stub).
    5. Store the agent's reply and send it back via the WhatsApp API.
    """
    try:
        body: dict[str, Any] = await req.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    # --- 1. Extract phone_number_id and message ---
    phone_number_id, from_number, text_body = _extract_payload(body)

    if not phone_number_id:
        # Not a message event (e.g. status update) — acknowledge
        return {"ok": True}

    if not text_body:
        return {"ok": True}

    # --- 2. Resolve tenant from phone_number_id ---
    tenant = await get_tenant_by_phone_number_id(session, phone_number_id)
    if not tenant:
        raise HTTPException(
            status_code=404,
            detail="No tenant configured for this phone_number_id",
        )

    # --- 3. Establish tenant context (enforces isolation for ALL queries) ---
    async with TenantContext(tenant.tenant_id):
        # Create or fetch the conversation
        conversation = await get_or_create_conversation(
            session,
            conversation_id=f"wa_{from_number}",
            channel="whatsapp",
            customer_phone=from_number,
        )

        # Store the inbound user message
        await add_message(
            session, conversation.conversation_id, "user", text_body
        )
        await session.commit()

        # --- 4. Dispatch to Hermes agent (V1 stub → echo) ---
        response_text, tool_calls = await dispatch_to_agent(
            tenant_id=tenant.tenant_id,
            conversation_id=conversation.conversation_id,
            message=text_body,
            session=session,
        )

        # Store the agent's response
        await add_message(
            session,
            conversation.conversation_id,
            "assistant",
            response_text,
            tool_calls=tool_calls,
        )
        await log_action(
            session,
            conversation.conversation_id,
            "webhook.message_received",
            tool_name=None,
            result="success",
        )
        await session.commit()

    # --- 5. Reply back to WhatsApp ---
    await send_whatsapp_message(
        phone_number_id=phone_number_id,
        to=from_number,
        text=response_text,
    )

    return {"ok": True, "reply": response_text}
