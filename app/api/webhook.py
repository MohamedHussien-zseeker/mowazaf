"""
WhatsApp + Telegram webhook endpoint.

Phase 0 changes:
- Async processing: webhook returns 200 OK immediately, Hermes runs in background.
- Idempotency: duplicate Meta webhook retries are detected and ignored.
- Per-conversation locking: jobs for the same conversation run sequentially.
- WhatsApp credentials stored in WhatsAppAccount table (not Tenant.config).

Architecture:
    WhatsApp → POST /webhook/whatsapp
                    ↓
            Idempotency check
                    ↓
            Save user message
                    ↓
            Enqueue PendingJob  ← 200 OK returned to Meta immediately
                    ↓
    Background Worker ← polls pending_jobs table
                    ↓
            Hermes agent
                    ↓
            Save assistant message
                    ↓
            WhatsApp API (send reply)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from ..config import settings
from ..db import TenantContext, ProcessedMessage, PendingJob, WhatsAppAccount
from ..crud import get_tenant_by_phone, get_tenant_by_telegram_token, get_or_create_conversation, add_message

router = APIRouter()


async def get_db_session() -> AsyncSession:
    from ..db import SessionLocal
    async with SessionLocal() as session:
        yield session


# ── WhatsApp GET (webhook verification) ──────────────────────────────────────────

@router.get("/whatsapp")
async def verify_whatsapp_webhook(req: Request):
    """WhatsApp webhook verification — matches MOWAZAF_WHATSAPP_VERIFY_TOKEN."""
    from ..config import settings
    hub_mode = req.query_params.get("hub.mode", "")
    hub_verify_token = req.query_params.get("hub.verify_token", "")
    hub_challenge = req.query_params.get("hub.challenge", "")
    if hub_mode == "subscribe" and hub_verify_token == settings.WHATSAPP_VERIFY_TOKEN:
        return int(hub_challenge)
    raise HTTPException(status_code=403, detail="Verification failed")


# ── WhatsApp POST (async — returns immediately) ──────────────────────────────────

@router.post("/whatsapp")
async def receive_whatsapp_message(
    req: Request,
    session: AsyncSession = Depends(get_db_session),
):
    """
    Receive an inbound WhatsApp message.

    Returns 200 OK immediately after:
    1. Deduplication (idempotency check)
    2. Saving the user message to DB
    3. Enqueueing a PendingJob

    Meta requires a response within ~10s. Hermes processing happens in the
    background worker so this endpoint always responds fast.
    """
    # Read raw body first so we can verify Meta webhook signatures.
    raw_body = await req.body()
    try:
        body = json.loads(raw_body.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # ── Meta webhook signature verification (P0) ─────────────────────────
    # If META_APP_SECRET is configured, we require X-Hub-Signature-256.
    # Format: "sha256=<hex>".
    if settings.META_APP_SECRET:
        sig = req.headers.get("X-Hub-Signature-256")
        if not sig or not sig.startswith("sha256="):
            raise HTTPException(status_code=403, detail="Missing/invalid signature")

        expected_hex = hmac.new(
            settings.META_APP_SECRET.encode("utf-8"),
            raw_body,
            hashlib.sha256,
        ).hexdigest()
        expected = f"sha256={expected_hex}"
        if not hmac.compare_digest(sig, expected):
            raise HTTPException(status_code=403, detail="Signature mismatch")

    # ── Extract event ─────────────────────────────────────────────────────────
    entry = body.get("entry", [])
    if not entry:
        return {"ok": True, "reason": "no_entry"}

    changes = entry[0].get("changes", [])
    if not changes:
        return {"ok": True, "reason": "no_changes"}

    value = changes[0].get("value", {})
    metadata = value.get("metadata", {})
    phone_number_id = metadata.get("phone_number_id")

    messages = value.get("messages", [])
    if not messages:
        return {"ok": True, "reason": "no_messages"}

    msg = messages[0]
    external_msg_id = msg.get("id", "")          # e.g. "wamid.xxx"
    from_number = msg.get("from", "")          # customer's phone
    message_type = msg.get("type", "text")
    text_body = (
        msg.get("text", {}).get("body", "")
        if message_type == "text"
        else json.dumps(msg.get(message_type, {}))
    )

    if not text_body.strip():
        return {"ok": True, "reason": "empty_message"}

    # ── Idempotency: is this message already processed? ─────────────────────
    # Meta retries failed webhooks. We skip already-processed messages.
    existing = await _get_processed(session, external_msg_id)
    if existing:
        # Already processed — safe to return 200
        return {"ok": True, "reason": "duplicate_skipped", "job_id": existing.job_id}

    # Mark as processing
    await _mark_processing(session, external_msg_id)

    # ── Resolve tenant via phone_number_id ────────────────────────────────────
    # First check the new WhatsAppAccount table, then fall back to Tenant.phone_number
    wa_account = await _get_wa_account(session, phone_number_id)
    if wa_account:
        tenant_id = wa_account.tenant_id
    else:
        # Legacy fallback: look up by Tenant.phone_number
        tenant = await get_tenant_by_phone(session, phone_number_id)
        if not tenant:
            await _mark_failed(session, external_msg_id, "no_tenant")
            raise HTTPException(
                status_code=404,
                detail="No tenant configured for this phone number",
            )
        tenant_id = tenant.tenant_id

    # ── Save user message + dispatch ────────────────────────────────────────
    job_id = f"wa_{secrets.token_hex(8)}"
    conv_id = f"wa_{from_number}"

    with TenantContext(tenant_id):
        conversation = await get_or_create_conversation(
            session,
            conversation_id=conv_id,
            channel="whatsapp",
            customer_phone=from_number,
        )
        await add_message(session, conv_id, "user", text_body)

        if settings.SYNC_DISPATCH:
            # Test mode: dispatch synchronously so tests don't need a worker.
            from ..hermes_client import dispatch_to_agent
            response_text, _ = await dispatch_to_agent(
                tenant_id=tenant_id,
                conversation_id=conv_id,
                message=text_body,
                session=session,
            )
            await add_message(session, conv_id, "assistant", response_text)
            await session.commit()
            return {"ok": True, "mode": "sync", "reply": response_text}

        await session.commit()

    # ── Enqueue background job ───────────────────────────────────────────────
    job = PendingJob(
        job_id=job_id,
        channel="whatsapp",
        tenant_id=tenant_id,
        conversation_id=conv_id,
        payload={
            "from_number": from_number,
            "text_body": text_body,
            "external_msg_id": external_msg_id,
            "message_type": message_type,
            "wa_account_id": getattr(wa_account, "id", None) if wa_account else None,
        },
        status="pending",
    )
    session.add(job)
    await session.commit()

    # Return 200 immediately — Meta is satisfied
    return {"ok": True, "job_id": job_id}


# ── Telegram GET ────────────────────────────────────────────────────────────────

@router.get("/telegram")
async def verify_telegram_webhook(
    hub_mode: str = "",
    hub_verify_token: str = "",
    hub_challenge: str = "",
):
    """Telegram webhook verification."""
    from ..config import settings
    if hub_mode == "subscribe" and hub_verify_token == settings.TELEGRAM_VERIFY_TOKEN:
        return int(hub_challenge)
    raise HTTPException(status_code=403, detail="Verification failed")


# ── Telegram POST (async) ──────────────────────────────────────────────────

@router.post("/telegram")
async def receive_telegram_update(
    req: Request,
    session: AsyncSession = Depends(get_db_session),
):
    """Receive a Telegram update. Returns immediately after enqueuing."""
    try:
        body = await req.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    message = body.get("message")
    if not message:
        return {"ok": True, "reason": "no_message"}

    update_id = str(body.get("update_id", ""))
    if not update_id:
        return {"ok": True, "reason": "no_update_id"}

    chat = message.get("chat", {})
    chat_id = str(chat.get("id", ""))
    text_body = message.get("text", "").strip()
    first_name = message.get("from", {}).get("first_name", "")

    if not text_body:
        return {"ok": True, "reason": "empty_message"}

    # ── Idempotency (Telegram uses update_id) ──────────────────────────────
    existing = await _get_processed(session, update_id)
    if existing:
        return {"ok": True, "reason": "duplicate_skipped", "job_id": existing.job_id}

    await _mark_processing(session, update_id)

    # ── Resolve tenant by Telegram chat_id ─────────────────────────────────
    tenant = await get_tenant_by_telegram_token(session, chat_id)
    if not tenant:
        await _mark_failed(session, update_id, "no_tenant")
        raise HTTPException(
            status_code=404,
            detail="No tenant configured for this Telegram bot",
        )

    tenant_id = tenant.tenant_id
    conv_id = f"tg_{chat_id}"
    job_id = f"tg_{secrets.token_hex(8)}"

    # ── Save user message ──────────────────────────────────────────────────
    with TenantContext(tenant_id):
        conversation = await get_or_create_conversation(
            session,
            conversation_id=conv_id,
            channel="telegram",
            customer_phone=chat_id,
            customer_name=first_name,
        )
        await add_message(session, conv_id, "user", text_body)
        await session.commit()

    # ── Enqueue background job ──────────────────────────────────────────────
    job = PendingJob(
        job_id=job_id,
        channel="telegram",
        tenant_id=tenant_id,
        conversation_id=conv_id,
        payload={
            "chat_id": chat_id,
            "text_body": text_body,
            "external_msg_id": update_id,
            "first_name": first_name,
        },
        status="pending",
    )
    session.add(job)
    await session.commit()

    return {"ok": True, "job_id": job_id}


# ── Idempotency helpers ─────────────────────────────────────────────────────

async def _get_processed(session: AsyncSession, external_id: str) -> Optional[ProcessedMessage]:
    """Check if a message has already been processed."""
    from sqlalchemy import select
    stmt = select(ProcessedMessage).where(
        ProcessedMessage.external_message_id == external_id
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def _mark_processing(session: AsyncSession, external_id: str, job_id: str = ""):
    """Record that a message is being (or has been) processed."""
    record = ProcessedMessage(
        external_message_id=external_id,
        job_id=job_id or None,
        status="processing",
    )
    session.add(record)
    await session.flush()


async def _mark_failed(session: AsyncSession, external_id: str, error: str):
    """Mark a message as failed (already processed, no action taken)."""
    from sqlalchemy import select
    stmt = select(ProcessedMessage).where(
        ProcessedMessage.external_message_id == external_id
    )
    result = await session.execute(stmt)
    record = result.scalar_one_or_none()
    if record:
        record.status = "failed"
    else:
        record = ProcessedMessage(
            external_message_id=external_id,
            status="failed",
        )
        session.add(record)
    await session.commit()


async def _get_wa_account(
    session: AsyncSession, phone_number_id: str
) -> Optional[WhatsAppAccount]:
    """Look up a WhatsApp account by Meta's phone_number_id."""
    from sqlalchemy import select
    stmt = select(WhatsAppAccount).where(
        WhatsAppAccount.phone_number_id == phone_number_id,
        WhatsAppAccount.is_active == True,
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()
