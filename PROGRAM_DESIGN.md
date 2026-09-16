# Mowazaf — Production Architecture & Roadmap
*Author: beblawy*
*Copyright (c) beblawy. All rights reserved.*

This document supersedes the earlier MVP-only program design. It defines the
target production architecture, the gaps against the current codebase, the
threat model, and a prioritized implementation roadmap.

---

## 1. Product position

Mowazaf is not an "AI chatbot". It is a multi-tenant **AI Employee platform**
for businesses on WhatsApp/Telegram/Web.

Core promise:
> each tenant gets an AI employee that knows the business, can execute approved
> actions, and hands off to humans when needed.

---

## 2. Current-state audit

### Strengths
- per-tenant `tenant_id` exists on core tables
- `TenantContext` + `_guard()` enforce SQL-level tenant scoping
- async webhook → `PendingJob` → worker already implemented
- `ProcessedMessage` provides idempotency storage
- per-tenant WhatsApp credentials modeled in `WhatsAppAccount`
- JWT embeds `tenant_id`

### Production blockers
- duplicate model ownership: `app/db.py` vs root `models.py`/`database.py`
- webhook signature validation missing
- no secrets encryption for `WhatsAppAccount.access_token`
- no usage/billing limits/token budgets
- no backup/restore/DR runbook
- no monitoring beyond `/health`
- AI plane can still influence control plane via DB writes if expanded

---

## 3. Target production architecture

```
Meta WhatsApp Cloud API
         │
         ▼
 Mowazaf Control Plane              Hermes AI Plane
 ┌────────────────────┐             ┌────────────────────┐
 │ API Gateway / Auth │             │ Model Router       │
 │ Webhooks           │             │ Agent Runtime      │
 │ Tenant Resolver    │             │ Memory / Tools     │
 │ Job Queue          │             │ Reasoning          │
 │ Conversation Mgmt  │             └────────────────────┘
 │ Tool Authorization │                     │
 │ Audit / Billing    │   bounded request   │
 │ Notifications      │ ───────────────────►│
 └─────────┬──────────┘                     │
           │                               ▼
           ▼                        OmniRoute / LLMs
      PostgreSQL
           │
           ▼
        Redis Queue
           │
           ▼
      Workers
```

Rules:
- webhook returns 200 immediately
- Hermes only returns `reply` + `tool_requests`
- control plane validates/authorizes/executes tool calls
- no tenant data crosses tenant boundaries

---

## 4. Data model changes

Already in place:
- `tenants`, `conversations`, `messages`, `knowledge_docs`, `agent_actions`
- `processed_messages`, `pending_jobs`
- `whatsapp_accounts`

Add:
- `subscriptions` or usage/quota fields on `tenants`
- tool permission/allowlist table
- webhook event delivery logs
- billing ledger

Security additions:
- `WhatsAppAccount.access_token` must be encrypted at rest
- unique `(tenant_id, external_message_id)` at DB level
- audit log immutable append pattern

---

## 5. Threat model (high level)

| Threat | Impact | Mitigation |
|--------|--------|-----------|
| tenant data leak | high | SQL tenant scoping + auth + no broad queries |
| forged webhook | high | Meta signature verification + reject unknowns |
| duplicate webhook | medium | idempotency table + unique constraint |
| AI cost spike | high | token/message limits + model fallback + quota |
| credential exposure | high | encrypted secrets + never log tokens |
| AI hallucination | medium | knowledge grounding + fallback message |
| tool abuse | high | control-plane authorization + human approval for sensitive tools |
| DB lock/unavailability | medium | retries + worker queue + graceful degradation |
| free model dependency | medium | fallback model chain + cost guardrails |

---

## 6. Migration plan: SQLite → PostgreSQL

1. make app DB URL fully configurable
2. adopt Alembic
3. keep SQLite support for tests/local dev
4. enforce migrations in CI before production deploy
5. add DB-level row-security-friendly schema patterns

---

## 7. Prioritized implementation roadmap

### P0 — Green + pilot hardening
1. keep tests green
2. remove duplicate model layer confusion
3. add webhook signature validation
4. enforce unique `(tenant_id, external_message_id)`
5. secrets hygiene review

### P1 — Production readiness
6. PostgreSQL default + Alembic
7. RBAC across knowledge/agent/tool endpoints
8. usage tracking + monthly quota enforcement
9. structured logs + queue depth alerts
10. backup/restore runbook

### P2 — SaaS features
11. control-plane vs AI-plane strict split
12. human handoff/resume flow
13. per-tenant onboarding + Embedded Signup scaffold
14. knowledge base ingestion + grounding
15. billing/subscription tables + limits

### P3 — Scale
16. Redis queue when pending jobs exceed SQLite performance
17. monitoring stack: Prometheus/Grafana or equivalent
18. disaster recovery testing cadence
19. multi-region backup strategy if needed

---

## 8. Done criteria for “production pilot”

- 3 paying pilot tenants onboarded through per-tenant WABA/number
- p95 webhook→reply latency under SLA
- zero cross-tenant data leak in tenant-isolation tests
- Hermes failure does not lose messages
- billing/limits enforced and tested
- restore tested end-to-end

---

## 9. Principles

- fail closed on secrets
- default deny on permissions
- never trust the model with direct data access
- every action is auditable
- build for replacement of components, not permanence
- ship tested increments, not plans
