"""Slice 5 — test the /api/agent/conversations/test endpoint (tenant_sika only).

This endpoint is used by the customer dashboard "Test Agent" feature.
It requires a JWT Bearer token scoped to tenant_sika.

Run:
  cd Mowazaf && .venv/Scripts/python.exe -m pytest tests/test_slice5_api_test_agent.py -v
"""

import pytest
from sqlalchemy import select

from app.auth import create_access_token
from app.db import Message


@pytest.mark.asyncio
async def test_slice5_api_test_agent(test_client, test_session):
    # Mint a test JWT for tenant_sika
    token = create_access_token({"sub": "test_user"}, tenant_id="tenant_sika")

    headers = {"Authorization": f"Bearer {token}"}
    body = {"message": "Hello, this is a test from Slice 5"}

    # POST /api/agent/conversations/test
    resp = await test_client.post(
        "/api/agent/conversations/test",
        json=body,
        headers=headers,
    )
    assert resp.status_code == 200, f"Non-200: {resp.status_code} {resp.text}"

    data = resp.json()
    assert data["role"] == "assistant"
    assert isinstance(data["content"], str)
    assert len(data["content"].strip()) > 0, "Expected non-empty assistant reply"

    # Verify DB saved user + assistant messages
    msgs = (await test_session.execute(select(Message).order_by(Message.id))).scalars().all()
    user_msgs = [m for m in msgs if m.role == "user"]
    assistant_msgs = [m for m in msgs if m.role == "assistant"]
    assert len(user_msgs) >= 1, f"Expected user messages in DB, got {len(user_msgs)}"
    assert len(assistant_msgs) >= 1, f"Expected assistant messages in DB, got {len(assistant_msgs)}"

    print(f"✅ Slice 5 passed — assistant reply: {data['content'][:60]!r}")
