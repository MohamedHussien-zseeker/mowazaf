"""
Hermes Client — dispatches messages to the correct tenant's Hermes agent.

Phase 1: Stateless text-only dispatch via the Hermes /v1/chat/completions endpoint.
No tool execution — the agent responds as pure text.

Retry logic: exponential backoff on 5xx / connection errors. 4xx errors fail immediately.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .crud import get_conversation_messages, log_action
from .db import Message, Tenant, get_current_tenant

logger = logging.getLogger("mowazaf.hermes_client")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 1.0  # seconds; doubles each attempt
REQUEST_TIMEOUT = 120.0   # seconds — LLM responses can be slow


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_session_key(tenant_id: str, conversation_id: str) -> str:
    """Hermes session key format: '{tenant_id}:{conversation_id}'."""
    return f"{tenant_id}:{conversation_id}"


def _messages_from_history(rows: list[Message]) -> list[dict[str, str]]:
    """Convert DB Message rows into OpenAI-format messages array."""
    return [{"role": m.role, "content": m.content} for m in rows]


def _build_system_message(tenant: Tenant) -> Optional[str]:
    """
    Build a system prompt from the tenant's config JSON.

    Expected config shape (Phase 1):
        {
            "system_prompt": "You are …",
            "agent_name": "AcmeBot",
            "language": "ar",
            …
        }
    Returns None if config is missing or has no system_prompt key.
    """
    cfg = tenant.config or {}
    prompt = cfg.get("system_prompt")
    if prompt:
        return prompt

    # Fallback: compose a minimal prompt from available fields
    agent_name = cfg.get("agent_name", "the assistant")
    language = cfg.get("language")
    parts = [f"You are {agent_name}."]
    if language:
        parts.append(f"Respond in {language}.")
    composed = " ".join(parts)
    return composed if composed != "You are the assistant." else None


def _api_url() -> str:
    """Base URL with trailing slash stripped, then /v1/chat/completions appended."""
    base = settings.HERMES_API_URL.rstrip("/")
    return f"{base}/v1/chat/completions"


# Model keys to look up in tenant.config, in priority order.
MODEL_CONFIG_KEYS = ("model", "agent_model", "hermes_model")
DEFAULT_MODEL = "hermes"


def _resolve_model(tenant: Tenant) -> str:
    """
    Resolve the Hermes model the tenant's config requests.

    Priority order over tenant.config (the Tenants.config JSON column):
        'model' -> 'agent_model' -> 'hermes_model'
    Validation: the value must be a string; it is stripped of surrounding
    whitespace; empty/whitespace-only values are rejected. If no key exists
    or every candidate is invalid, falls back to 'hermes' (phase-1 default).
    """
    cfg = tenant.config or {}

    for key in MODEL_CONFIG_KEYS:
        raw = cfg.get(key)
        if raw is None:
            continue
        if not isinstance(raw, str):
            logger.warning(
                "tenant.config['%s'] is not a string (%s); skipping model key %r",
                key, type(raw).__name__, key,
            )
            continue
        model = raw.strip()
        if not model:
            logger.warning("tenant.config['%s'] is empty; skipping model key %r", key, key)
            continue
        return model

    return DEFAULT_MODEL


# ---------------------------------------------------------------------------
# Core dispatch
# ---------------------------------------------------------------------------

async def dispatch_to_agent(
    tenant_id: str,
    conversation_id: str,
    message: str,
    session: AsyncSession,
) -> tuple[str, Optional[dict]]:
    """
    Dispatch a message to the Hermes agent for the given tenant.

    Returns (response_text, tool_calls).
    Phase 1: tool_calls is always None — no tool execution.
    """
    start = time.monotonic()
    api_url = _api_url()
    api_key = settings.HERMES_API_KEY

    if not api_key:
        latency_ms = int((time.monotonic() - start) * 1000)
        await log_action(
            session,
            conversation_id=conversation_id,
            action="dispatch:hermes",
            result="error",
            latency_ms=latency_ms,
        )
        raise ValueError("HERMES_API_KEY is not configured")

    # ── 1. Load tenant config & system prompt ───────────────────────────
    stmt = select(Tenant).where(Tenant.tenant_id == tenant_id)
    result = await session.execute(stmt)
    tenant = result.scalar_one_or_none()

    if tenant is None:
        latency_ms = int((time.monotonic() - start) * 1000)
        await log_action(
            session,
            conversation_id=conversation_id,
            action="dispatch:load_tenant",
            result="error",
            latency_ms=latency_ms,
        )
        return _error_response("Tenant not found"), None

    system_prompt = _build_system_message(tenant)

    # Resolve which Hermes model this tenant's config requests.
    model = _resolve_model(tenant)
    if model != DEFAULT_MODEL:
        logger.info(
            "dispatch_to_agent: tenant %s uses model %r (keys %s)",
            tenant_id, model, "+".join(MODEL_CONFIG_KEYS),
        )

    # ── 2. Build messages array from conversation history ───────────────
    history_rows = await get_conversation_messages(session, conversation_id)
    messages: list[dict[str, str]] = []

    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})

    messages.extend(_messages_from_history(history_rows))

    # The current user message may not yet be persisted; ensure it's included.
    if not messages or messages[-1].get("content") != message:
        messages.append({"role": "user", "content": message})

    # ── 3. HTTP dispatch with retry ─────────────────────────────────────
    session_key = _build_session_key(tenant_id, conversation_id)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-Hermes-Session-Key": session_key,
    }
    payload = {
        "model": model,
        "messages": messages,
    }

    last_error: Optional[Exception] = None
    response_text = ""

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                resp = await client.post(api_url, json=payload, headers=headers)

            if resp.status_code >= 500:
                # Server error — retry
                raise httpx.HTTPStatusError(
                    f"Server error {resp.status_code}",
                    request=resp.request,
                    response=resp,
                )

            if resp.status_code >= 400:
                # By default we don't retry 4xx, but Hermes sometimes forwards
                # upstream/provider failures (e.g. "Model is unavailable")
                # as HTTP 400. Those are transient, so we treat them like
                # retryable server errors.
                body = resp.text[:2000]
                body_json = None
                try:
                    body_json = resp.json()
                except Exception:
                    body_json = None

                upstream_msg = ""
                if isinstance(body_json, dict):
                    upstream_msg = (
                        body_json.get("error", {})
                        .get("message", "")
                        or ""
                    )
                retryable_unavailable = (
                    "Model is unavailable" in upstream_msg
                    or "Model is unavailable" in body
                )

                if retryable_unavailable:
                    logger.warning(
                        "Hermes retryable 4xx (model unavailable) on attempt %d/%d: %s",
                        attempt,
                        MAX_RETRIES,
                        upstream_msg or body[:300],
                    )
                    # Raise to hit the retry handler.
                    raise httpx.HTTPStatusError(
                        f"{resp.status_code} model unavailable",
                        request=resp.request,
                        response=resp,
                    )

                # Non-retryable 4xx
                logger.error(
                    "Hermes API 4xx on attempt %d: %s %s",
                    attempt, resp.status_code, body[:500],
                )
                latency_ms = int((time.monotonic() - start) * 1000)
                await log_action(
                    session,
                    conversation_id=conversation_id,
                    action="dispatch:hermes",
                    result="error",
                    latency_ms=latency_ms,
                )
                return _error_response(
                    f"Hermes API returned {resp.status_code}: {body[:500]}"
                ), None

            # ── 4. Parse OpenAI-format response ────────────────────────
            data = resp.json()
            response_text = (
                data.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
            )

            # Log token usage if the API returns it
            usage = data.get("usage", {})
            tokens_used = usage.get("total_tokens")

            latency_ms = int((time.monotonic() - start) * 1000)
            await log_action(
                session,
                conversation_id=conversation_id,
                action="dispatch:hermes",
                result="success",
                tokens_used=tokens_used,
                latency_ms=latency_ms,
            )
            return response_text, None  # Phase 1: no tool_calls

        except (httpx.HTTPStatusError, httpx.ConnectError, httpx.ReadTimeout) as exc:
            last_error = exc
            wait = RETRY_BACKOFF_BASE * (2 ** (attempt - 1))
            logger.warning(
                "Hermes dispatch attempt %d/%d failed: %s — retrying in %.1fs",
                attempt, MAX_RETRIES, exc, wait,
            )
            await asyncio.sleep(wait)

    # All retries exhausted
    latency_ms = int((time.monotonic() - start) * 1000)
    await log_action(
        session,
        conversation_id=conversation_id,
        action="dispatch:hermes",
        result="error",
        latency_ms=latency_ms,
    )
    return _error_response(
        f"Hermes API unreachable after {MAX_RETRIES} attempts: {last_error}"
    ), None


# ---------------------------------------------------------------------------
# Tool-aware variant (stub for Phase 2)
# ---------------------------------------------------------------------------

async def dispatch_with_tools(
    tenant_id: str,
    conversation_id: str,
    message: str,
    session: AsyncSession,
    allowed_tools: list[str],
) -> tuple[str, Optional[dict]]:
    """
    Dispatch with a specific allowlist of tools for this tenant.

    Phase 2: will pass tool definitions to the Hermes API and execute
    tool calls in a loop. For now, delegates to the text-only path
    and ignores the allowlist.
    """
    return await dispatch_to_agent(tenant_id, conversation_id, message, session)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _error_response(detail: str) -> str:
    """Return a user-facing error string. Logs the detail server-side."""
    logger.error("dispatch_to_agent: %s", detail)
    return f"[Agent Error] {detail}"
