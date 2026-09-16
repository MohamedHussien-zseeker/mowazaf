"""
Background job processor for Mowazaf.

Polls pending_jobs table and processes one job at a time per conversation
(to preserve message order). Runs as a separate process alongside uvicorn.

Usage:
    python -m app.worker

Architecture:
    pending_jobs (SQLite)
          ↓
    Worker polls for 'pending' jobs
          ↓
    Per-conversation lock (prevents race conditions)
          ↓
    Hermes dispatch
          ↓
    Save assistant message
          ↓
    WhatsApp/Telegram outbound API
          ↓
    Mark job done / failed
          ↓
    Loop
"""
from __future__ import annotations

import asyncio
import logging
import secrets
import time
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, update, text
from sqlalchemy.ext.asyncio import AsyncSession

from .db import (
    SessionLocal,
    PendingJob,
    TenantContext,
    Conversation,
    Message,
)
from .hermes_client import dispatch_to_agent
from .channels import dispatch_reply
from .crud import add_message

logger = logging.getLogger("mowazaf.worker")

# How long a worker holds a conversation lock before releasing it
LOCK_TIMEOUT_SECONDS = 60

# How long to wait between poll cycles
POLL_INTERVAL_SECONDS = 1.0

# Max jobs per conversation before releasing lock (prevents stuck locks)
MAX_JOBS_PER_LOCK = 10


def _worker_id() -> str:
    return f"worker-{secrets.token_hex(4)}"


async def poll_and_process(session: AsyncSession) -> bool:
    """
    Pick up one pending job and process it.

    Uses per-conversation locking: only one worker processes a conversation at a time.
    This preserves message order within a conversation while allowing parallel
    processing across different conversations.

    Returns True if a job was processed, False if none were available.
    """
    now = datetime.now(timezone.utc)
    worker_id = _worker_id()

    # ── Claim a job: find a pending job whose conversation is not locked ────
    # Subquery: conversations that are currently locked
    locked_subq = (
        select(PendingJob.conversation_id)
        .where(PendingJob.locked_until != None)
        .where(PendingJob.locked_until > now)
        .distinct()
    )

    stmt = (
        select(PendingJob)
        .where(PendingJob.status == "pending")
        .where(PendingJob.conversation_id.not_in(locked_subq))
        .order_by(PendingJob.created_at.asc())
        .limit(1)
    )
    result = await session.execute(stmt)
    job: Optional[PendingJob] = result.scalar_one_or_none()

    if job is None:
        return False  # Nothing to do

    # ── Claim the job ──────────────────────────────────────────────────
    job.status = "processing"
    job.worker_id = worker_id
    job.started_at = now
    job.locked_until = datetime.fromtimestamp(
        now.timestamp() + LOCK_TIMEOUT_SECONDS, tz=timezone.utc
    )
    await session.commit()

    logger.info(
        "[%s] Claimed job %s (channel=%s, conv=%s)",
        worker_id, job.job_id, job.channel, job.conversation_id,
    )

    try:
        await _process_job(session, job)
        job.status = "done"
        job.completed_at = datetime.now(timezone.utc)
        job.locked_until = None
        logger.info("[%s] Job %s done ✓", worker_id, job.job_id)
    except Exception as exc:
        job.status = "failed"
        job.error = str(exc)[:500]
        job.locked_until = None
        logger.exception("[%s] Job %s failed: %s", worker_id, job.job_id, exc)

    await session.commit()
    return True


async def _process_job(session: AsyncSession, job: PendingJob):
    """Process a single job: dispatch to Hermes, save reply, send outbound."""
    payload = job.payload
    channel = job.channel
    tenant_id = job.tenant_id
    conv_id = job.conversation_id
    text_body = payload.get("text_body", "")
    external_msg_id = payload.get("external_msg_id", "")

    with TenantContext(tenant_id):
        # ── Dispatch to Hermes ───────────────────────────────────────────
        response_text, tool_calls = await dispatch_to_agent(
            tenant_id=tenant_id,
            conversation_id=conv_id,
            message=text_body,
            session=session,
        )

        # ── Save assistant reply ───────────────────────────────────────
        await add_message(session, conv_id, "assistant", response_text, tool_calls=tool_calls)
        await session.commit()

    # ── Send reply back via the channel ─────────────────────────────
    wa_account_id = payload.get("wa_account_id")

    if channel == "whatsapp":
        recipient = payload.get("from_number", "")
        sent = await dispatch_reply(
            tenant=None,
            channel="whatsapp",
            recipient=recipient,
            text=response_text,
            wa_account_id=wa_account_id,
            session=session,
        )
        if not sent:
            logger.warning(
                "[worker] send_whatsapp failed for job %s → %s",
                job.job_id, recipient,
            )
    elif channel == "telegram":
        recipient = payload.get("chat_id", "")
        sent = await dispatch_reply(
            tenant=None,
            channel="telegram",
            recipient=recipient,
            text=response_text,
            session=session,
        )
        if not sent:
            logger.warning(
                "[worker] send_telegram failed for job %s → %s",
                job.job_id, recipient,
            )


async def run_worker():
    """Main worker loop — poll, process, repeat."""
    logger.info("Mowazaf worker starting…")

    while True:
        try:
            async with SessionLocal() as session:
                processed = await poll_and_process(session)
                if not processed:
                    await asyncio.sleep(POLL_INTERVAL_SECONDS)
        except Exception:
            logger.exception("[worker] Poll cycle failed, sleeping 5s")
            await asyncio.sleep(5.0)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)-25s %(levelname)-8s %(message)s",
    )
    asyncio.run(run_worker())
