"""Send one Slice-3-style webhook message for tenant_sika and report results."""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import shutil

# Fresh temp DB + sync mode
tmpdir = tempfile.mkdtemp(prefix="mowazaf_slice3_msg1_")
TEST_DB_PATH = os.path.join(tmpdir, "test_mowazaf.sqlite")
TEST_DB_URL = f"sqlite+aiosqlite:///{TEST_DB_PATH}"
os.environ["MOWAZAF_DATABASE_URL"] = TEST_DB_URL
os.environ["MOWAZAF_SYNC_DISPATCH"] = "true"
os.environ["MOWAZAF_SECRET"] = "test_secret_token_urlsafe_32_output_for_slice3_only"

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db import Base, Tenant, Message
from app.main import app
from app.api.webhook import get_db_session


async def main() -> int:
    engine = create_async_engine(TEST_DB_URL, echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def _session():
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_db_session] = _session

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
        ])
        await session.commit()

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
                                    "from": "+201****0001",
                                    "id": "wamid_tenant_sika_1",
                                    "timestamp": "1690000002",
                                    "type": "text",
                                    "text": {"body": "Slice 3 real dispatch message 1 for tenant_sika"},
                                }
                            ],
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/webhook/whatsapp", json=payload)
        print("STATUS", resp.status_code)
        print("BODY", resp.text)

    async with session_factory() as session:
        msgs = (await session.execute(select(Message).order_by(Message.id))).scalars().all()
        print("TOTAL_MESSAGES", len(msgs))
        for m in msgs:
            print(f"{m.id}\t{m.tenant_id}\t{m.role}\t{(m.content or '')[:160]}")

    await engine.dispose()
    shutil.rmtree(tmpdir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
