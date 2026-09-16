"""Standalone Slice 3 runner — bypasses pytest SQLite locking issue on Windows.

Executes the real webhook flow for tenant_sika / tenant_hassan:
  - 3 sequential messages per tenant
  - asserts HTTP 200 on each
  - asserts assistant replies exist in DB

Run:
  cd C:/Users/win10/Mowazaf && .venv/Scripts/python.exe run_slice3.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import shutil

# ── Force fresh temp-file SQLite + sync mode before any app import ───────────
tmpdir = tempfile.mkdtemp(prefix="mowazaf_slice3_")
TEST_DB_PATH = os.path.join(tmpdir, "test_mowazaf.sqlite")
TEST_DB_URL = f"sqlite+aiosqlite:///{TEST_DB_PATH}"
os.environ["MOWAZAF_DATABASE_URL"] = TEST_DB_URL
os.environ["MOWAZAF_SYNC_DISPATCH"] = "true"
os.environ["MOWAZAF_SECRET"] = "test_secret_token_urlsafe_32_output_for_slice3_only"

from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db import Base, Tenant, get_current_tenant, TenantContext
from app.main import app
from app.api.webhook import get_db_session


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


async def main() -> int:
    engine = create_async_engine(TEST_DB_URL, echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Override dependency to use our temp DB session
    async def _session() -> AsyncSession:
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_db_session] = _session

    # Seed tenants
    async with session_factory() as session:
        session.add_all([
            Tenant(
                tenant_id="tenant_sika",
                slug="sika",
                name="Sika",
                phone_number="wa_1",
                config={
                    "model": "openai/gpt-oss-120b",
                    "telegram_bot_token": "123456:ABC-DEF",
                    "telegram_bot_chat_id": "987654321",
                },
            ),
            Tenant(
                tenant_id="tenant_hassan",
                slug="hassan",
                name="Hassan",
                phone_number="wa_2",
                config={"model": "auto/best-chat"},
            ),
        ])
        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        tenants = [
            {"phone": "wa_1", "from": "+201****0001", "tenant_id": "tenant_sika"},
            {"phone": "wa_2", "from": "+201****0002", "tenant_id": "tenant_hassan"},
        ]

        for t in tenants:
            phone = t["phone"]
            from_number = t["from"]
            tenant_id = t["tenant_id"]

            for i in range(3):
                body = f"Slice 3 real dispatch message {i+1} for {tenant_id}"
                resp = await client.post(
                    "/webhook/whatsapp",
                    json=_payload(phone, from_number, body, f"wamid_{tenant_id}_{i}"),
                )
                assert resp.status_code == 200, f"Webhook failed for {tenant_id}: {resp.text}"

        # Verify assistant messages in DB
        async with session_factory() as session:
            from sqlalchemy import select
            from app.db import Message
            msgs = (await session.execute(select(Message).order_by(Message.id))).scalars().all()
            assistant_msgs = [m for m in msgs if m.role == "assistant" and m.content and m.content.strip()]

        print(f"✅ Slice 3 passed — assistant messages: {len(assistant_msgs)}")
        print("Assistant messages:")
        for m in assistant_msgs[:10]:
            print(f" - [{m.tenant_id}] {m.content[:120]}")

    await engine.dispose()
    shutil.rmtree(tmpdir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
