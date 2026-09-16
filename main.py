"""
Mowazaf — Multi-tenant AI Agent Platform (V1 MVP).

Architecture (V1):
    Channels (WhatsApp/Telegram/Web) → FastAPI Gateway → Tenant Router
        → Hermes Agent (stub) → Tenant-scoped DB (PostgreSQL)

Tenant isolation is enforced at the SQL level: every query is filtered by
``tenant_id``.  The JWT carries ``tenant_id``; the WhatsApp webhook resolves
``tenant_id`` from ``phone_number_id``.  No query can read or write across
tenant boundaries.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

from config import settings
from database import init_db

# ── Fail-closed on secret ──────────────────────────────────────────────────

if not settings.SECRET:
    raise RuntimeError(
        "MOWAZAF_SECRET is not set. Generate one with:\n"
        '  python -c "import secrets; print(secrets.token_urlsafe(32))"\n'
        "and add MOWAZAF_SECRET=<value> to .env"
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize database tables on startup."""
    await init_db()
    yield


app = FastAPI(
    title="Mowazaf — AI Agent Platform",
    version="0.1.0",
    description="Multi-tenant SaaS platform for delivering AI agents to customers",
    lifespan=lifespan,
)

# ── CORS — explicit origins only ───────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

# ── Routers ────────────────────────────────────────────────────────────────
from api.auth import router as auth_router         # noqa: E402
from api.webhook import router as webhook_router     # noqa: E402
from api.agent import router as agent_router         # noqa: E402

app.include_router(auth_router, prefix="/api/auth", tags=["auth"])
app.include_router(agent_router, prefix="/api/agent", tags=["agent"])
app.include_router(webhook_router, prefix="/webhook", tags=["webhook"])


# ── Endpoints ──────────────────────────────────────────────────────────────


@app.get("/health")
async def health():
    return {"status": "ok", "service": "mowazaf", "version": "0.1.0"}


@app.get("/debug/whatsapp_callback_url")
async def whatsapp_callback_url():
    """Return the full public WhatsApp webhook callback URL.

    This is configuration-driven (no trycloudflare hardcoding):
    - PUBLIC_BASE_URL
    - WHATSAPP_WEBHOOK_PATH
    """
    base = (settings.PUBLIC_BASE_URL or "").rstrip("/")
    path = settings.WHATSAPP_WEBHOOK_PATH
    if not base:
        return {"public_base_url": "", "whatsapp_webhook_path": path, "callback_url": ""}
    return {"public_base_url": base, "whatsapp_webhook_path": path, "callback_url": f"{base}{path}"}


@app.get("/debug/token")
async def debug_token():
    from config import settings
    import os
    return {
        "WHATSAPP_VERIFY_TOKEN": settings.WHATSAPP_VERIFY_TOKEN,
        "DOTENV_LOADED": os.environ.get("MOWAZAF_WHATSAPP_VERIFY_TOKEN", "NOT_SET"),
    }


@app.get("/debug/test_alias")
async def test_alias(hub_verify_token: str = Query(default="", alias="hub.verify_token")):
    return {"hub_verify_token": hub_verify_token}


@app.get("/")
async def root():
    return {
        "name": "Mowazaf",
        "type": "AI Agent Platform",
        "version": "0.1.0",
        "status": "running",
    }
