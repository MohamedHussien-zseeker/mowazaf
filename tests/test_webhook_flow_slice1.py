"""
test_webhook_flow_slice1.py — VERTICAL SLICE 1
Goal: prove webhook → tenant resolve → DB log (user message) → dispatch → assistant save.

Run:
    cd Mowazaf && .venv/Scripts/python.exe -m pytest tests/test_webhook_flow_slice1.py -v
"""
import pytest
from sqlalchemy import select
from app.db import Message


@pytest.mark.asyncio
async def test_whatsapp_webhook_tenant_resolve_and_db_log(test_client, test_session):
    """
    Vertical slice 1: full happy path, real NAS OmniRoute auto/best-chat.

    Steps verified:
      1. POST /webhook/whatsapp with valid WhatsApp payload
      2. Tenant resolved from phone_number_id="wa_1" (→ tenant_sika in test_db seed)
      3. User message row inserted into DB (role='user')
      4. dispatch_to_agent called (→ NAS OmniRoute auto/best-chat)
      5. Assistant message row inserted into DB (role='assistant')
      6. HTTP 200 returned
    """
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
                                    "from": "+201000000001",
                                    "id": "wamid_1",
                                    "timestamp": "1690000001",
                                    "type": "text",
                                    "text": {"body": "Hello, how can you help me?"},
                                }
                            ],
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }

    # Dispatch the webhook request
    resp = await test_client.post("/webhook/whatsapp", json=payload)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    # ── DB assertions: read from in-memory test DB ─────────────────────────────────
    msgs = (await test_session.execute(select(Message).order_by(Message.id))).scalars().all()

    user_msgs = [m for m in msgs if m.role == "user"]
    assistant_msgs = [m for m in msgs if m.role == "assistant"]

    # Step 2+3: at least one user message should exist
    assert len(user_msgs) >= 1, f"Expected ≥1 user message rows, got {len(user_msgs)}"
    assert user_msgs[0].content == "Hello, how can you help me?"

    # Step 4+5: at least one assistant (agent) response should exist
    assert len(assistant_msgs) >= 1, (
        f"Expected ≥1 assistant message rows (real OmniRoute dispatch), got {len(assistant_msgs)}. "
        "Check NAS OmniRoute reachability at 192.168.1.2:20128."
    )

    print(f"\n✅ Slice 1 passed — {len(user_msgs)} user msg(s), {len(assistant_msgs)} assistant msg(s)")
