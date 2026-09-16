# Mowazaf — WhatsApp Business AI Auto-Response Plan

**Author:** beblawy
**Copyright (c) beblawy. All rights reserved.**
**Created:** 2026-09-10

---

## ⚠️ Ownership (corrected 2026-09-10)

The WhatsApp Business number **`01119978553` belongs to beblawy** (the account owner). It is **NOT** Sika's number. `tenant_sika` is only a *legacy sample tenant slug* inside Mowazaf's database — it does **not** mean Sika owns the number. Sika is out of scope for this setup.

**This document describes Mowazaf's own pipeline** (its per-tenant webhook → worker → OmniRoute). It is **separate from the new Hermes-native WhatsApp Cloud setup** (the official Meta Business Cloud API adapter, `hermes whatsapp-cloud`, where Hermes replies directly and which reuses the existing Mowazaf `.env` Meta credentials). Mowazaf's multitenant **code** (seeds, tests, tenant slugs) is intentionally **untouched** — only the ownership facts in the docs were corrected.

### Setup intent (decided 2026-09-10)
- Number owner: **beblawy** (`01119978553`)
- Goal: anyone who messages that number → **Hermes replies** (official Business Cloud API, no ban risk)
- Path: **Hermes native `whatsapp-cloud`** — separate from Mowazaf
- Credentials: **reuse** the Meta values already in `C:\Users\win10\Mowazaf\.env` (`MOWAZAF_WHATSAPP_TOKEN`, `MOWAZAF_WHATSAPP_PHONE_ID`, `MOWAZAF_WHATSAPP_VERIFY_TOKEN`)
- Status: **files fixed only — no setup run yet** (per beblawy's instruction)

---

Make Mowazaf automatically respond to **anyone** who messages beblawy's WhatsApp Business number (`01119978553`) via an AI agent powered by Hermes/OmniRoute.

---

## Current State (as of 2026-09-10)

| Component | Status | Notes |
|---|---|---|
| Mowazaf server (port 8000) | **DOWN** | Needs restart: `uvicorn app.main:app --host 0.0.0.0 --port 8000` |
| Cloudflare tunnel | **DEAD** | Quick tunnel URLs are ephemeral; needs new tunnel + Meta callback URL update |
| Meta app `hermes` | **Development mode** | Only testers/admins/developers trigger webhooks. External numbers are blocked until app is published to Live. |
| OmniRoute | **Running** | `192.168.1.2:20128` — verified reachable |
| `hermes_client.py` | **Phase 1** | Text-only OpenAI-format dispatch via OmniRoute (no tools, no skills) |
| Tenant `tenant_sika` | **Configured** | Mapped to business number `01119978553`, system prompt + model set |
| Worker | **NOT running** | Depends on server being up |
| Tests | **7/7 passing** | Last verified 2026-09-05 |

---

## Architecture Recap

```
Real WhatsApp user
    │
    ▼
Meta WhatsApp Cloud API
    │ (webhook POST)
    ▼
Cloudflare Tunnel ──→ Mowazaf (port 8000)
    │
    ├─ /webhook/whatsapp  →  resolve tenant from phone_number_id
    │                          →  create PendingJob
    │
    ▼
Worker (polls pending_jobs)
    │
    ├─ dispatch_to_agent()  →  OmniRoute (/v1/chat/completions)
    │                          →  tenant system prompt + conversation history
    │
    ├─ Save assistant reply to DB
    │
    └─ send_whatsapp()  →  Meta Graph API  →  reply to sender
```

---

## Phase 1 — Get the Pilot Alive Again (1-2 hours)

### Step 1: Start Mowazaf server
```bash
cd C:\Users\win10\Mowazaf
uvicorn app.main:app --host 0.0.0.0 --port 8000
```
Verify: `curl http://localhost:8000/health` → `{"status":"ok","service":"mowazaf","version":"0.1.0"}`

### Step 2: Start the worker
```bash
cd C:\Users\win10\Mowazaf
python -m app.worker
```

### Step 3: Start a new Cloudflare tunnel
```bash
cloudflared tunnel --url http://localhost:8000
```
Copy the new `https://*.trycloudflare.com` URL.

### Step 4: Update .env
Set `MOWAZAF_PUBLIC_BASE_URL` to the new tunnel URL in `C:\Users\win10\Mowazaf\.env`.

### Step 5: Verify callback URL
```bash
curl http://localhost:8000/debug/whatsapp_callback_url
```
Confirm the `callback_url` matches the new tunnel.

### Step 6: Update Meta dashboard
1. Go to Meta Developer Dashboard → App `hermes`
2. WhatsApp → Configuration → Webhook
3. Paste the new callback URL (`https://<new>.trycloudflare.com/webhook/whatsapp`)
4. Paste the verify token
5. Click **Verify and Save**
6. Confirm `messages` subscription is active

### Step 7: Test with a tester number
- Send WhatsApp from `01010969009` → `01119978553`
- Expected: Mowazaf replies automatically with AI response
- Check DB for `pending_jobs` + `messages` rows for correct tenant

---

## Phase 2 — Publish Meta App to Live (~30 min + 1-3 day review)

This is the **critical gate** for "anyone can message you."

### Step 1: Request permissions
In Meta Dashboard → App Review → Permissions and Features:
- Request `whatsapp_business_messaging` permission
- Fill the usage description: "AI-powered customer service assistant that auto-replies to WhatsApp messages on behalf of businesses"

### Step 2: Submit for review
- Submit the app for review (typically 1-3 business days)
- Once approved, the restriction on non-tester senders is lifted

### Step 3: Switch to Live mode
- Toggle app status from Development → Live
- Now **any WhatsApp user** messaging `01119978553` triggers the webhook

### No code changes needed
Mowazaf already handles any sender number. The Development mode restriction is the only thing blocking non-testers.

---

## Phase 3 — Production Hardening (me, ~2 hours)

### P0 — Must fix before real customers

| Issue | Fix | Impact |
|---|---|---|
| Quick tunnel = random URL on restart | Switch to **named Cloudflare tunnel** (persistent subdomain) OR deploy Mowazaf on NAS behind a fixed endpoint | Tunnel dying = dead webhook |
| Meta access token expires | Set up **Meta System User long-lived token** or implement token refresh cron | Silent failure on sends |
| No webhook signature verification | Wire `META_APP_SECRET` for `X-Hub-Signature-256` validation | Spoofed webhooks accepted |

### P1 — Should fix soon

| Issue | Fix | Impact |
|---|---|---|
| Worker runs as bare background process | Run under **Docker Compose** with auto-restart (Postgres + Redis + App + Worker) | Process crash = silent failure |
| SQLite DB (single file) | Fine for 1 tenant; migrate to **PostgreSQL** when adding tenants | Concurrency + durability |
| No conversation memory limit | Add context windowing (last N messages) so conversations don't grow unbounded | Token cost explosion |
| No rate limiting | Add per-sender + per-tenant rate limits | Abuse / cost spike |
| No error notifications | Wire failed job alerts to Telegram | Silent failures |

### P2 — Future improvements

| Issue | Fix | Impact |
|---|---|---|
| `hermes_client.py` = Phase 1 text-only | Phase 2: pass **skills + tools** per-tenant | Richer agent responses |
| Single business number | Multi-tenant Meta Embedded Signup flow | Scalable SaaS |
| No admin dashboard | Build web UI for tenant management, conversation viewing, billing | Product completeness |

---

## Key Files Reference

| File | Purpose |
|---|---|
| `C:\Users\win10\Mowazaf\.env` | All secrets and config (server, Meta, OmniRoute, tunnel URL) |
| `C:\Users\win10\Mowazaf\config.py` | Pydantic settings — loads from .env with `MOWAZAF_` prefix |
| `C:\Users\win10\Mowazaf\app\main.py` | FastAPI app entry point, router registration |
| `C:\Users\win10\Mowazaf\app\hermes_client.py` | Hermes/OmniRoute dispatch — builds messages, calls /v1/chat/completions |
| `C:\Users\win10\Mowazaf\app\channels.py` | Outbound senders (WhatsApp Graph API, Telegram Bot API) |
| `C:\Users\win10\Mowazaf\app\worker.py` | Background job processor — polls pending_jobs, dispatches, sends replies |
| `C:\Users\win10\Mowazaf\app\api\webhook.py` | WhatsApp webhook handler — GET (verify) + POST (receive messages) |
| `C:\Users\win10\Mowazaf\app\db.py` | SQLAlchemy models, TenantContext, SessionLocal |
| `C:\Users\win10\Mowazaf\docker-compose.yml` | Postgres + Redis + App containers |
| `C:\Users\win10\Mowazaf\WHATSAPP_SETUP_CHECKPOINT.md` | Step-by-step checkpoint for WhatsApp pilot setup |

---

## Key Config Values

| Key | Location | Notes |
|---|---|---|
| `MOWAZAF_SECRET` | .env | App secret — required, app won't start without it |
| `MOWAZAF_HERMES_API_URL` | .env | OmniRoute: `http://192.168.1.2:20128` |
| `MOWAZAF_HERMES_API_KEY` | .env | OmniRoute API key (hermes key, not ultron) |
| `MOWAZAF_WHATSAPP_TOKEN` | .env | Meta Graph API token for sending replies |
| `MOWAZAF_WHATSAPP_PHONE_ID` | .env | Business phone_number_id |
| `MOWAZAF_WHATSAPP_VERIFY_TOKEN` | .env | Verify token for Meta webhook handshake |
| `MOWAZAF_META_APP_SECRET` | .env | For X-Hub-Signature-256 verification |
| `MOWAZAF_PUBLIC_BASE_URL` | .env | Tunnel URL — update on every tunnel restart |
| Tenant `tenant_sika` | DB | Mapped to `01119978553`, config has system prompt + model `auto/best-reasoning` |

---

## Two Paths (Decision Record)

### Path A — Mowazaf (SaaS product)
- Full multi-tenant architecture, conversation DB, admin API
- More work to productionize, but it IS the product
- Sika = pilot customer #1
- Future: sell to other businesses

### Path B — Hermes native WhatsApp bot
- Hermes has built-in `platforms.whatsapp` — configure and done
- Zero code, just config
- NO multi-tenant, NO conversation DB, NO per-business isolation
- Good for personal use, NOT for selling

**Decision: Path A** (Mowazaf is the product being built for Sika)

---

## Emergency Runbook

| Symptom | Fix |
|---|---|
| Tunnel died | Restart `cloudflared tunnel --url http://localhost:8000` → update Meta callback URL |
| Server died | `cd C:\Users\win10\Mowazaf && uvicorn app.main:app --host 0.0.0.0 --port 8000` |
| Worker died | `cd C:\Users\win10\Mowazaf && python -m app.worker` |
| Stale processes | Kill duplicate uvicorn/worker processes before restarting |
| OmniRoute unreachable | Check `curl http://192.168.1.2:20128/health` — NAS may need Docker restart |
| Webhook verify fails | Check tunnel URL matches Meta dashboard; verify token must match `.env` |
| Replies not sending | Check Meta access token hasn't expired; check `send_whatsapp` logs |
