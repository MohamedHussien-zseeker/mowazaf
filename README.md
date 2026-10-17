# Mowazaf — Multi-tenant AI Agent Platform (V1 MVP)

[![Build](https://github.com/MohamedHussien-zseeker/mowazaf/actions/workflows/ci.yml/badge.svg)](https://github.com/MohamedHussien-zseeker/mowazaf/actions/workflows/ci.yml)
[![Tests](https://img.shields.io/badge/tests-7%20passing-brightgreen)](https://github.com/MohamedHussien-zseeker/mowazaf)
[![License](https://img.shields.io/badge/license-Custom-blue)](./LICENSE)

> **Status: V1 proof-of-concept.** Public mirror for portfolio/review.
> Production canon (deployment secrets, infra config) lives in the
> private sibling repo [`mowazaf-prod`](https://github.com/MohamedHussien-zseeker/mowazaf-prod).

## Overview

Mowazaf is a multi-tenant SaaS platform that delivers AI agents to customers
through multiple channels (WhatsApp, Telegram, Web). Incoming messages from
any channel hit a FastAPI API gateway, which resolves the tenant and routes
the message to that tenant's Hermes agent.

**V1 scope**: single server, single PostgreSQL database, one WhatsApp number,
one happy customer. The focus is on proving the tenant-isolation pattern and
the webhook → agent → reply flow.

## Architecture (V1)

```
Channels (WhatsApp)
        │
        ▼
FastAPI Gateway ──→ /webhook/whatsapp
        │              │
        │              ├── phone_number_id → tenant lookup
        │              └── TenantContext(tenant_id)
        │
        ▼
CRUD layer (all queries filtered by tenant_id at SQL level)
        │
        ▼
Hermes Agent (stub → echo, real dispatch in V2)
        │
        ▼
WhatsApp Cloud API (reply)
```

## Key Design Decisions

- **Single database, tenant_id on every table**: V1 uses one PostgreSQL DB
  with a `tenant_id` column on every tenant-scoped table. Every query in
  `crud.py` includes `WHERE tenant_id = <current>`.
- **Tenant context enforcement**: `TenantContext` (a ContextVar) is set at
  the start of every request. The `_guard()` function in `database.py`
  raises `RuntimeError` if any query is attempted without an active context.
- **JWT carries tenant_id**: The token payload includes `tenant_id`, which is
  extracted by `get_current_user` and used to bind the DB context.
- **Webhook routing**: The WhatsApp webhook is unauthenticated. The tenant
  is resolved from `phone_number_id` in the payload, then `TenantContext`
  is established for all subsequent operations.
- **Hermes dispatch is stubbed**: `hermes_client.py` echoes back the message.
  Replace `dispatch_to_agent` with a real Hermes runtime call in V2.

## Project Structure

```
mowazaf/
├── main.py              # FastAPI app, CORS, router registration
├── models.py            # SQLAlchemy models (tenant_id on every table)
├── database.py          # Engine, session, TenantContext, init_db
├── config.py            # Pydantic settings (fail-closed on SECRET)
├── auth.py              # JWT + password hashing + role checks
├── crud.py              # Tenant-scoped DB operations
├── hermes_client.py     # Hermes dispatch (V1 stub → echo)
├── api/
│   ├── __init__.py
│   ├── auth.py          # POST /api/auth/login
│   ├── webhook.py       # GET/POST /webhook/whatsapp
│   └── agent.py         # GET/POST /api/agent/conversations/...
├── skills/
│   └── SOUL.md          # Default agent personality
├── tests/
├── docker-compose.yml   # postgres + redis + app
├── requirements.txt
├── setup.sh             # venv + deps bootstrap script
└── .env.example
```

## Setup

```bash
# 1. Bootstrap (creates .venv and installs deps with uv)
chmod +x setup.sh
./setup.sh

# 2. Configure
cp .env.example .env
# Edit .env — set MOWAZAF_SECRET, PostgreSQL URL, WhatsApp credentials

# 3. Start services (PostgreSQL + Redis)
docker compose up -d postgres redis

# 4. Apply schema + seed a test tenant
uv run --python .venv/Scripts/python.exe -c "
import asyncio
from database import init_db, SessionLocal, TenantContext
from models import Tenant, User
from auth import hash_password

async def seed():
    await init_db()
    async with SessionLocal() as s:
        async with TenantContext('tenant_demo'):
            t = Tenant(tenant_id='tenant_demo', name='Demo Shop',
                       slug='demo', phone_number_id='1234567890',
                       phone_number='+15551234567')
            s.add(t)
            u = User(tenant_id='tenant_demo', username='admin',
                     full_name='Admin', role='admin',
                     password_hash=hash_password('changeme'))
            s.add(u)
        await s.commit()
    print('Seeded tenant_demo + admin user')

asyncio.run(seed())
"

# 5. Run the server
uv run --python .venv/Scripts/python.exe uvicorn main:app --reload --port 8000
```

## API

### Auth

```
POST /api/auth/login
Content-Type: application/json

{"username": "admin", "password": "changeme"}

→ 200 {"access_token": "<jwt>", "token_type": "bearer"}
```

The JWT payload includes `tenant_id`, `role`, and `user_id`.

### Agent (requires Bearer token)

```
GET  /api/agent/conversations           # list tenant's conversations
GET  /api/agent/conversations/{id}      # get conversation + messages
POST /api/agent/conversations/{id}      # send message → agent reply
POST /api/agent/conversations/test      # test endpoint (ephemeral convo)
```

### Webhook (unauthenticated — routed by phone_number_id)

```
GET  /webhook/whatsapp?hub.mode=subscribe&hub.verify_token=...&hub.challenge=...
POST /webhook/whatsapp          # WhatsApp Cloud API payload
```

## Tenant Isolation

Every model with tenant data has a `tenant_id: Mapped[str]` column indexed
for query performance. The enforcement chain is:

1. **Web request** → `TenantContext(tenant_id)` is entered
2. **CRUD call** → `_guard()` checks context is set, then adds
   `WHERE tenant_id = <current>` to every SELECT/UPDATE/DELETE
3. **SQL execution** → SQLAlchemy rejects any row outside the tenant

This is tested by:
- Creating data for two tenants
- Querying as one tenant and confirming the other's data is invisible
