"""Slice 4 concurrency msg 1 for tenant_sika."""
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
async def test_slice4_tenant_sika_msg1(test_client, test_session):
    resp = await test_client.post(
        "/webhook/whatsapp",
        json=_payload("wa_1", "+201****0001", "Slice 4 concurrency msg 1 for tenant_sika", "slice4_tenant_sika_1"),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    print({"status": resp.status_code, "body": body})
