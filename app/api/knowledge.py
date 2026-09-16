"""
Knowledge base management API.

Customers upload PDFs, FAQs, product catalogs, policies.
Documents are parsed, chunked, embedded, and stored in the tenant's vector DB.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user
from ..crud import add_knowledge_doc, list_knowledge_docs
from ..db import SessionLocal, TenantContext, KnowledgeDoc

router = APIRouter()


class KnowledgeDocOut(BaseModel):
    id: int
    filename: str
    doc_type: str
    content: str
    source_url: Optional[str] = None
    created_at: datetime


@router.post("/", status_code=status.HTTP_201_CREATED)
async def upload_document(
    file: UploadFile = File(...),
    doc_type: str = "faq",
    source_url: Optional[str] = None,
    session: AsyncSession = Depends(SessionLocal),
    user: dict = Depends(get_current_user),
):
    """Upload a document to the tenant's knowledge base."""
    async with TenantContext(user["tenant_id"]):
        content = await file.read()
        text = content.decode("utf-8", errors="replace")

        doc = await add_knowledge_doc(
            session=session,
            filename=file.filename or "uploaded",
            doc_type=doc_type,
            content=text,
            source_url=source_url,
        )
        await session.commit()
        await session.refresh(doc)

        # TODO: Parse, chunk, embed, and store in vector DB (pgvector)
        # For V1 we store raw content; V2 will add embedding pipeline

        return {"id": doc.id, "filename": doc.filename, "doc_type": doc.doc_type, "status": "ingested"}


@router.get("/", response_model=list[KnowledgeDocOut])
async def list_documents(
    doc_type: Optional[str] = None,
    session: AsyncSession = Depends(SessionLocal),
    user: dict = Depends(get_current_user),
):
    """List all documents in the tenant's knowledge base."""
    async with TenantContext(user["tenant_id"]):
        docs = await list_knowledge_docs(session, doc_type=doc_type)
        return [
            {
                "id": d.id,
                "filename": d.filename,
                "doc_type": d.doc_type,
                "content": d.content[:500],  # Truncate for list view
                "source_url": d.source_url,
                "created_at": d.created_at,
            }
            for d in docs
        ]


@router.get("/{doc_id}", response_model=KnowledgeDocOut)
async def get_document(
    doc_id: int,
    session: AsyncSession = Depends(SessionLocal),
    user: dict = Depends(get_current_user),
):
    """Get a specific document."""
    async with TenantContext(user["tenant_id"]):
        stmt = select(KnowledgeDoc).where(KnowledgeDoc.id == doc_id)
        result = await session.execute(stmt)
        doc = result.scalar_one_or_none()
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        return doc


@router.delete("/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    doc_id: int,
    session: AsyncSession = Depends(SessionLocal),
    user: dict = Depends(get_current_user),
):
    """Delete a document from the tenant's knowledge base."""
    async with TenantContext(user["tenant_id"]):
        stmt = select(KnowledgeDoc).where(KnowledgeDoc.id == doc_id)
        result = await session.execute(stmt)
        doc = result.scalar_one_or_none()
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        await session.delete(doc)
        await session.commit()
        return None