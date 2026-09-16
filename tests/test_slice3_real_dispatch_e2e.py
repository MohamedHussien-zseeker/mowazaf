"""Slice 3 — real dispatch end-to-end (no mocking).

Runs the real webhook flow twice:
- wa_1 (tenant_sika)
- wa_2 (tenant_hassan)

Sends 3 sequential messages per tenant and asserts:
- HTTP 200
- DB has at least one assistant message per tenant

Run:
  cd Mowazaf && .venv/Scripts/python.exe -m pytest tests/test_slice3_real_dispatch_e2e.py -v

NOTE: This test hits your real Hermes/OmniRoute stack.
"""

import pytest
from sqlalchemy import select

from app.db import Message


def _payload(phone_number_id: str, from_number: str, msg_body: str, msg_id: str):
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "entry1",
                "changes": [
                    {
                        "value": {
                            "metadata": {"phone_number_id": phone_number_id},
                            "messages": [
                                {
                                    "from": from_number,
                                    "id": msg_id,
                                    "timestamp": "1690000002",
                                    "type": "text",
                                    "text": {"body": msg_body},
                                }
                            ],
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }


@pytest.mark.asyncio
async def test_slice3_real_dispatch_e2e(test_client, test_session):
    tenants = [
        {"phone": "wa_1", "from": "+201000000001", "tenant_id": "tenant_sika"},
        {"phone": "wa_2", "from": "+201000000002", "tenant_id": "tenant_hassan"},
    ]

    for t in tenants:
        phone = t["phone"]
        from_number = t["from"]
        tenant_id = t["tenant_id"]

        # Send 3 sequential messages
        for i in range(3):
            body = f"Slice 3 real dispatch message {i+1} for {tenant_id}"
            resp = await test_client.post(
                "/webhook/whatsapp",
                json=_payload(phone, from_number, body, f"wamid_{tenant_id}_{i}"),
            )
            assert resp.status_code == 200, f"Webhook failed for {tenant_id}: {resp.text}"

    # Assert assistant messages exist in DB
    msgs = (await test_session.execute(select(Message).order_by(Message.id))).scalars().all()
    assistant_msgs = [m for m in msgs if m.role == "assistant" and m.content and m.content.strip()]
    assert len(assistant_msgs) >= 2, f"Expected assistant replies, got {len(assistant_msgs)}"

    print(f"✅ Slice 3 passed — assistant messages: {len(assistant_msgs)}")
