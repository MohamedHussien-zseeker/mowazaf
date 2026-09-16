"""
Mowazaf — Multi-tenant AI Agent Platform.

Tenant isolation is enforced at the SQL layer: every query MUST be filtered
by tenant_id. The database layer enforces this — the LLM never sees cross-tenant data.

Architecture (V1 MVP):
  Channels → FastAPI Gateway → Tenant Router → Hermes Agent → Tenant-scoped tools/memory
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .db import init_db

if settings.SECRET == "replace_me_with_secrets_token_urlsafe_32_output":
    raise RuntimeError(
        "MOWAZAF_SECRET is not set. Set a strong secret in .env before starting.\n"
        "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize database on startup."""
    await init_db()
    yield


app = FastAPI(
    title="Mowazaf — AI Agent Platform",
    version="0.1.0",
    description="Multi-tenant SaaS platform for delivering AI agents to customers",
    lifespan=lifespan,
)

# CORS — explicit origins only, never combine wildcard with allow_credentials
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

# Include routers
from .api.webhook import router as webhook_router
from .api.tenant import router as tenant_router
from .api.agent import router as agent_router
from .api.knowledge import router as knowledge_router

app.include_router(tenant_router, prefix="/api/tenants", tags=["tenants"])
app.include_router(agent_router, prefix="/api/agent", tags=["agent"])
app.include_router(knowledge_router, prefix="/api/knowledge", tags=["knowledge"])
app.include_router(webhook_router, prefix="/webhook", tags=["webhook"])


@app.get("/health")
async def health():
    return {"status": "ok", "service": "mowazaf", "version": "0.1.0"}


@app.get("/debug/whatsapp_callback_url")
async def whatsapp_callback_url():
    """Return the full public WhatsApp webhook callback URL.

    This is configuration-driven:
    - PUBLIC_BASE_URL (e.g. https://<tunnel>.trycloudflare.com)
    - WHATSAPP_WEBHOOK_PATH (/webhook/whatsapp)

    Never hard-code trycloudflare domains in code.
    """
    base = (settings.PUBLIC_BASE_URL or "").rstrip("/")
    path = settings.WHATSAPP_WEBHOOK_PATH
    if not base:
        return {"public_base_url": "", "whatsapp_webhook_path": path, "callback_url": ""}
    return {"public_base_url": base, "whatsapp_webhook_path": path, "callback_url": f"{base}{path}"}


@app.get("/")
async def root():
    return {"name": "Mowazaf", "type": "AI Agent Platform", "status": "running"}