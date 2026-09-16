# Mowazaf — WhatsApp Setup Checkpoint
**Saved: 2026-09-05 (session progress update)**
**Status: ✅ Pilot functional. Awaiting Meta tester invite + real-device test.**

---

## What's DONE (do not redo)

1. **WhatsApp webhook verification working** — local and public tunnel both return the challenge value ✅
2. **Server running** on `0.0.0.0:8000` with `uvicorn app.main:app` ✅
3. **Worker running** in the background; processes `PendingJob` entries per-conversation ✅
4. **Cloudflare tunnel**: `https://transcripts-threatened-rocky-strength.trycloudflare.com` ✅
   - Health: `{"status":"ok","service":"mowazaf","version":"0.1.0"}`
   - Webhook verify: returns challenge when token matches
5. **Meta app**: `hermes`, Business type, subscribed `messages v26.0`
6. **Tenant mapping fixed**:
   - Business WhatsApp number: `01119978553` — **beblawy's** number (owner, NOT Sika's)
   - Tenant `tenant_sika` mapped to `01119978553` via `WhatsAppAccount`
   - Test customer: `01010969009`
7. **Tenant config updated**:
   - model: `auto/best-reasoning`
   - system prompt / agent name / language set
8. **Hermes API routing fixed**:
   - `.env` now points to OmniRoute: `http://192.168.1.2:20128`
   - API key updated to valid OmniRoute key
   - Synthetic webhook test confirmed full pipeline: webhook → job → worker → Hermes → Arabic assistant reply saved to DB ✅
9. **Tests**: 7/7 passing after shared in-memory SQLite + Slice 6 patch target fix
10. **PROGRAM_DESIGN.md** rewritten as production architecture doc with threat model + P0/P1/P2/P3 roadmap

---

## Current State

- **Public webhook callback URL**: `https://transcripts-threatened-rocky-strength.trycloudflare.com/webhook/whatsapp`
- **Verify token**: `7NuJbVoW7XhnfEg5wiB_Ig`
- **Business number**: `01119978553`
- **Test customer**: `01010969009`
- **Tenant**: `tenant_sika` (owner of business number = beblawy; the number `01119978553` is beblawy's)
- **DB**: `sqlite+aiosqlite:///./mowazaf.db`
- **Worker**: background process polling `pending_jobs`
- **OmniRoute**: `http://192.168.1.2:20128` with working API key

---

## What's LEFT — Meta / real-device test

1. In Meta dashboard, confirm the callback URL is saved and `messages v26.0` is subscribed ✅
2. Add `01010969009` as a **Tester** in the Meta app if it isn't already
3. **beblawy** accepts the Meta tester invite from WhatsApp
4. Send a real WhatsApp message from `01010969009` → `01119978553`
5. Expected: Mowazaf replies automatically via AI

---

## Important Notes

- **Dev mode restriction**: Meta only delivers webhooks for Admins/Developers/Testers while the app is unpublished. Other numbers won't trigger webhooks until the app is published to Live.
- **Any sender is supported in production**, but right now only testers work.
- **Per-tenant numbers**: In production, each business will connect its own WhatsApp Business number via Meta Embedded Signup. Do not route all customers through one shared WhatsApp number.

---

## If Something Breaks

- **Tunnel died**: restart `cloudflared.exe tunnel`, update Meta callback URL
- **Server died**: restart with `uvicorn app.main:app --host 0.0.0.0 --port 8000`
- **Worker died**: restart with `python -m app.worker`
- **Stale processes**: stop duplicate uvicorn/worker processes before restarting
