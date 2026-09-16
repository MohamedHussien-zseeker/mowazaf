"""tests/test_model_router_slice2.py — VERTICAL SLICE 2

Slice 2 goal (A): validate the routing/model-resolution contract by
intercepting the Hermes dispatch layer and proving:
- webhook resolves tenant from phone_number_id
- webhook calls dispatch_to_agent with the correct tenant_id
- assistant message persisted uses the mocked dispatch response

No network calls: we patch app.hermes_client.dispatch_to_agent.

Run:
  cd Mowazaf && .venv/Scripts/python.exe -m pytest tests/test_model_router_slice2.py -v
"""

import pytest
from unittest.mock import patch
from sqlalchemy import select

from app.db import Message


@pytest.mark.asyncio
async def test_slice2_webhook_calls_dispatch_with_tenant_and_saves_mocked_reply(test_client, test_session):
    # The webhook handler imports dispatch_to_agent at runtime inside the function.
    # But it ultimately calls the symbol from app.hermes_client.
    async def fake_dispatch_to_agent(tenant_id: str, conversation_id: str, message: str, session):
        return (f"[MOCKED REPLY] tenant_id={tenant_id} msg={message}", None)

    with patch("app.hermes_client.dispatch_to_agent", side_effect=fake_dispatch_to_agent):
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "entry1",
                    "changes": [
                        {
                            "value": {
                                "metadata": {"phone_number_id": "wa_1"},
                                "messages": [
                                    {
                                        "from": "+201000000099",
                                        "id": "wamid_slice2",
                                        "timestamp": "1690000002",
                                        "type": "text",
                                        "text": {"body": "Slice 2 test message"},
                                    }
                                ],
                            },
                            "field": "messages",
                        }
                    ],
                }
            ],
        }

        resp = await test_client.post("/webhook/whatsapp", json=payload)
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

        msgs = (await test_session.execute(select(Message).order_by(Message.id))).scalars().all()
        assistant_msgs = [m for m in msgs if m.role == "assistant"]
        user_msgs = [m for m in msgs if m.role == "user"]

        assert any(m.content == "Slice 2 test message" for m in user_msgs), "User message not persisted"
        assert any(
            "[MOCKED REPLY]" in m.content and "tenant_id=tenant_sika" in m.content for m in assistant_msgs
        ), f"Mocked assistant not found. Assistant msgs: {[m.content for m in assistant_msgs]}"

    print("✅ Slice 2 passed — tenant resolved and dispatch mocked reply saved")
