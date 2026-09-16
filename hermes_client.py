"""Hermes client — bridge between the FastAPI gateway and the Hermes runtime.

V1: **stub** that echoes back the message (with a prefix).  Replace
``dispatch_to_agent`` with an HTTP / WebSocket call to the Hermes runtime
once the Hermes server is running.

The stub is intentionally minimal so the MVP can be tested end-to-end before
the real agent dispatch is wired in.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession


async def dispatch_to_agent(
    tenant_id: str,
    conversation_id: str,
    message: str,
    session: AsyncSession,
) -> tuple[str, Optional[dict]]:
    """Dispatch an inbound message to the tenant's Hermes agent.

    Parameters
    ----------
    tenant_id
        The public tenant identifier (from the JWT or tenant lookup).
    conversation_id
        The conversation thread key.
    message
        The inbound user message content.
    session
        DB session (available for the real dispatch to read knowledge docs,
        past messages, etc.).

    Returns
    -------
    ``(response_text, tool_calls)`` — ``tool_calls`` is ``None`` in the stub.
    """
    # ── V1 stub: echo back ──────────────────────────────────────────────────
    response = f"[Mowazaf Agent — tenant:{tenant_id}] Echo: {message}"
    tool_calls = None
    return response, tool_calls


async def dispatch_with_context(
    tenant_id: str,
    conversation_id: str,
    message: str,
    session: AsyncSession,
    system_prompt: Optional[str] = None,
) -> tuple[str, Optional[dict]]:
    """Dispatch with an optional system prompt.

    In V2 this loads the tenant's SOUL.md / skills / knowledge docs and builds
    a context-rich request to the Hermes runtime.
    """
    # Load tenant-specific skills / knowledge (placeholder for Phase 2)
    sp = system_prompt or ""
    if sp:
        message = f"[System: {sp}]\nUser: {message}"
    return await dispatch_to_agent(tenant_id, conversation_id, message, session)
