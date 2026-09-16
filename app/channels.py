"""
Channel dispatcher — unifies outbound message sending across all channels.

Each function sends a text reply via the channel's API and returns True/False.
They are called by the webhook handlers after dispatch_to_agent() returns.
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx

from .config import settings
from .db import Tenant

logger = logging.getLogger("mowazaf.channels")


# ---------------------------------------------------------------------------
# WhatsApp
# ---------------------------------------------------------------------------

# --------------------------------------------------------------------------#
# WhatsApp
# --------------------------------------------------------------------------#

async def send_whatsapp(
    tenant,
    to_phone: str,
    text: str,
    wa_account_id: Optional[int] = None,
    session=None,
) -> bool:
    """
    Send a WhatsApp text reply via the Meta Graph API.

    Credentials priority:
    1. WhatsAppAccount record (wa_account_id) — per-tenant WABA
    2. tenant.config['whatsapp_token'] / ['whatsapp_phone_id']
    3. settings.WHATSAPP_TOKEN / settings.WHATSAPP_PHONE_ID
    """
    token: Optional[str] = None
    phone_id: Optional[str] = None

    if wa_account_id and session:
        from sqlalchemy import select
        from .db import WhatsAppAccount
        stmt = select(WhatsAppAccount).where(WhatsAppAccount.id == wa_account_id)
        result = await session.execute(stmt)
        wa_acc = result.scalar_one_or_none()
        if wa_acc:
            token = wa_acc.access_token
            phone_id = wa_acc.phone_number_id

    if not token and tenant:
        token = tenant.config.get("whatsapp_token") or settings.WHATSAPP_TOKEN
        phone_id = tenant.config.get("whatsapp_phone_id") or settings.WHATSAPP_PHONE_ID
    elif not token:
        token = settings.WHATSAPP_TOKEN
        phone_id = settings.WHATSAPP_PHONE_ID

    if not token or not phone_id:
        logger.warning(
            "send_whatsapp: no credentials (wa_account=%s, tenant=%s, token=%s, phone_id=%s)",
            wa_account_id, getattr(tenant, "tenant_id", None), bool(token), bool(phone_id),
        )
        return False

    url = f"https://graph.facebook.com/v19.0/{phone_id}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to_phone,
        "type": "text",
        "text": {"body": text},
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
        if resp.status_code >= 300:
            logger.error(
                "send_whatsapp: HTTP %d → %s: %s",
                resp.status_code, to_phone, resp.text[:300],
            )
            return False
        logger.info("send_whatsapp: sent to %s", to_phone)
        return True
    except Exception as exc:
        logger.error("send_whatsapp: failed → %s: %s", to_phone, exc)
        return False


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

async def send_telegram(
    tenant,
    to_chat_id: str,
    text: str,
) -> bool:
    """
    Send a Telegram text message via the Bot API.

    Credentials come from tenant.config:
        telegram_bot_token — Bot API token from @BotFather
    """
    bot_token = None
    if tenant:
        bot_token = tenant.config.get("telegram_bot_token")

    if not bot_token:
        logger.warning(
            "send_telegram: no telegram_bot_token configured for tenant %s",
            getattr(tenant, "tenant_id", None),
        )
        return False

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": to_chat_id,
        "text": text,
        "parse_mode": "HTML",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=payload)
        data = resp.json()
        if not data.get("ok"):
            logger.error(
                "send_telegram: Telegram API error → %s: %s",
                to_chat_id, data.get("description", resp.text[:300]),
            )
            return False
        logger.info(
            "send_telegram: sent msg_id=%s to chat %s",
            data.get("result", {}).get("message_id"), to_chat_id,
        )
        return True
    except Exception as exc:
        logger.error("send_telegram: failed → %s: %s", to_chat_id, exc)
        return False


# --------------------------------------------------------------------------#
# Dispatcher
# --------------------------------------------------------------------------#

CHANNEL_SENDERS = {
    "whatsapp": send_whatsapp,
    "telegram": send_telegram,
}


async def dispatch_reply(
    tenant,
    channel: str,
    recipient: str,
    text: str,
    wa_account_id: Optional[int] = None,
    session=None,
) -> bool:
    """
    Send `text` to `recipient` via `channel`.

    Returns True if the send succeeded, False otherwise.
    Silently skips unknown channels.
    """
    sender = CHANNEL_SENDERS.get(channel)
    if not sender:
        logger.warning("dispatch_reply: unknown channel %r", channel)
        return False

    if channel == "whatsapp":
        return await send_whatsapp(tenant, recipient, text, wa_account_id, session)
    return await send_telegram(tenant, recipient, text)
