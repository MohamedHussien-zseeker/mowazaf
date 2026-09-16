"""Slice 4 — concurrency/load (real dispatch).

Runs small concurrent webhook calls across both tenants:
- wa_1 -> tenant_sika
- wa_2 -> tenant_hassan

Goal:
- ensure no OOM-style crash (no SIGKILL)
- keep all responses 200
- confirm assistant replies saved

Run:
  cd Mowazaf && .venv/Scripts/python.exe -m pytest tests/test_slice4_concurrency_load.py -v

Note:
  This is a lightweight load test (few requests) to stay MVP-safe.
"""

import asyncio
import time

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


async def _send_one(test_client, phone, from_number, tenant_label, i):
    body = f"Slice 4 concurrency msg {i+1} for {tenant_label}"
    start = time.time()
    resp = await test_client.post(
        "/webhook/whatsapp",
        json=_payload(phone, from_number, body, f"slice4_{tenant_label}_{i}"),
    )
    dt = time.time() - start
    return resp, dt, body


@pytest.mark.asyncio
async def test_slice4_concurrency_load_e2e(test_client, test_session):
    tenants = [
        {"phone": "wa_1", "from": "+201000000001", "label": "tenant_sika"},
        {"phone": "wa_2", "from": "+201000000002", "label": "tenant_hassan"},
    ]

    # Keep concurrency small to reduce OOM risk:
    # 2 parallel tasks per tenant (total 4 requests)
    per_tenant_messages = 2

    tasks = []
    for t in tenants:
        for i in range(per_tenant_messages):
            tasks.append(_send_one(test_client, t["phone"], t["from"], t["label"], i))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    for r in results:
        assert not isinstance(r, Exception), f"Request raised exception: {r}"
        resp, dt, _body = r
        assert resp.status_code == 200, f"Non-200: {resp.status_code} body={resp.text}"

    # Verify assistant replies exist (at least per request or at least >0)
    msgs = (await test_session.execute(select(Message).order_by(Message.id))).scalars().all()
    assistant_msgs = [m for m in msgs if m.role == "assistant" and m.content and m.content.strip()]
    assert len(assistant_msgs) >= 2, f"Expected assistant messages, got {len(assistant_msgs)}"

    print(f"✅ Slice 4 passed — assistant_msgs={len(assistant_msgs)}; concurrency_requests={len(tasks)}")
