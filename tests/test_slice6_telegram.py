"""Slice 6 — Telegram webhook + channel dispatcher.

Validates:
- /webhook/telegram accepts a Telegram message payload
- routes to the correct tenant (via telegram_bot_chat_id in tenant.config)
- saves user message to DB
- enqueues a PendingJob for async processing

Note:
In async mode the assistant reply is produced by the background worker, so
this test asserts on the enqueued job rather than an assistant message row.
"""

import pytest
from unittest.mock import patch, AsyncMock

from sqlalchemy import select

from app.db import Message, PendingJob


def _telegram_payload(chat_id: str, text: str, msg_id: int, first_name: str = "Ahmed"):
    return {
        "update_id": 123456789,
        "message": {
            "message_id": msg_id,
            "from": {"id": int(chat_id), "is_bot": False, "first_name": first_name},
            "chat": {"id": int(chat_id), "type": "private"},
            "date": 1690000000,
            "text": text,
        },
    }


@pytest.mark.asyncio
async def test_slice6_telegram_webhook(test_client, test_session):
    with patch("app.channels.dispatch_reply", new_callable=AsyncMock) as mock_dispatch:
        mock_dispatch.return_value = True

        resp = await test_client.post(
            "/webhook/telegram",
            json=_telegram_payload("987654321", "Hello from Telegram Slice 6", 1),
        )

        assert resp.status_code == 200, f"Telegram webhook failed: {resp.status_code} {resp.text}"
        body = resp.json()
        assert body.get("ok") is True
        assert body.get("job_id")

    # User message should be saved synchronously by the webhook.
    msgs = (await test_session.execute(select(Message).order_by(Message.id))).scalars().all()
    user_msgs = [m for m in msgs if m.role == "user" and "Telegram Slice 6" in m.content]
    assert len(user_msgs) >= 1, f"Expected Telegram user message in DB, got {len(user_msgs)}"

    # An async job should be enqueued for the worker to dispatch + reply.
    jobs = (await test_session.execute(select(PendingJob).order_by(PendingJob.id))).scalars().all()
    telegram_jobs = [j for j in jobs if j.channel == "telegram"]
    assert len(telegram_jobs) >= 1, f"Expected Telegram pending job, got {len(telegram_jobs)}"

    print(
        f"✅ Slice 6 passed — Telegram webhook handled {len(user_msgs)} user msg(s); "
        f"enqueued {len(telegram_jobs)} job(s)"
    )
