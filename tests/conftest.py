# conftest.py — Mowazaf test configuration
#
# Invariant: all tests use ONE shared in-memory SQLite database via
# `sqlite+aiosqlite:///file::memory:?cache=shared&uri=true` so async/concurrent
# tests never hit "database is locked".

import json
import os

# Patch pydantic_settings CORS decoding before settings are loaded.
from pydantic_settings.sources import (
    DotEnvSettingsSource,
    EnvSettingsSource,
    PydanticBaseEnvSettingsSource,
)
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import StaticPool


def _safe_decode(self, field_name: str, field, value: str):
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        if isinstance(value, str) and "," in value:
            return [v.strip() for v in value.split(",") if v.strip()]
        raise


for _cls in (
    DotEnvSettingsSource,
    EnvSettingsSource,
    PydanticBaseEnvSettingsSource,
):
    _cls.decode_complex_value = _safe_decode

os.environ["MOWAZAF_DATABASE_URL"] = "sqlite+aiosqlite:///file::memory:?cache=shared&uri=true"
os.environ["MOWAZAF_SYNC_DISPATCH"] = "true"

import pytest
import asyncio
from contextlib import asynccontextmanager
from httpx import ASGITransport, AsyncClient
from fastapi import FastAPI

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# ── Shared test engine/session ──────────────────────────────────────────────
_TEST_DB_URL = "sqlite+aiosqlite:///file::memory:?cache=shared&uri=true"
_test_engine = create_async_engine(
    _TEST_DB_URL,
    echo=False,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_TestSessionLocal = async_sessionmaker(_test_engine, class_=AsyncSession, expire_on_commit=False)


async def _get_test_session():
    async with _TestSessionLocal() as session:
        yield session


# Make the app use the same in-memory DB/session factory during tests.
from app import db as _app_db  # noqa: E402
_app_db.engine = _test_engine
_app_db.SessionLocal = _TestSessionLocal

from app.db import Base  # noqa: E402


@asynccontextmanager
async def test_lifespan(app_: FastAPI):
    async with _test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield


# Build test app from the real app.
from app.main import app as real_app  # noqa: E402

test_app = FastAPI(title="Mowazaf Test", lifespan=test_lifespan)
for route in real_app.routes:
    test_app.routes.append(route)

# Override webhook DB dependency.
from app.api.webhook import get_db_session  # noqa: E402

test_app.dependency_overrides[get_db_session] = _get_test_session


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
async def test_client():
    async with _test_engine.begin() as conn:
        for attempt in range(1, 4):
            try:
                await conn.run_sync(Base.metadata.drop_all)
                await conn.run_sync(Base.metadata.create_all)
                break
            except OperationalError as exc:
                if "database is locked" in str(exc) and attempt < 3:
                    await asyncio.sleep(0.5 * attempt)
                    continue
                raise

    async with _TestSessionLocal() as session:
        from app.db import Tenant  # noqa: E402
        session.add_all(
            [
                Tenant(
                    tenant_id="tenant_sika",
                    slug="sika",
                    name="Sika",
                    phone_number="wa_1",
                    config={
                        "model": "oc/deepseek-v4-flash-free",
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
            ]
        )
        await session.commit()

    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
async def test_session(test_client):
    async with _TestSessionLocal() as session:
        yield session
