"""Repository layer: async DB operations for Document."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import exists, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from industrial_rag.db.models import Document, DocumentStatus, KBOperationLease, UpdateJob


class DocumentRepository:
    """Async CRUD for Document rows."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, **values: Any) -> Document:
        doc = Document(**values)
        self._session.add(doc)
        await self._session.flush()
        return doc

    async def get(self, doc_id: str) -> Document | None:
        return await self._session.get(Document, doc_id)

    async def get_by_kb_and_id(self, kb_id: str, doc_id: str) -> Document | None:
        stmt = select(Document).where(
            Document.id == doc_id, Document.knowledge_base_id == kb_id
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def list_by_kb(
        self,
        kb_id: str,
        *,
        include_deleted: bool = False,
        status_filter: DocumentStatus | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> list[Document]:
        stmt = select(Document).where(Document.knowledge_base_id == kb_id)
        if not include_deleted:
            stmt = stmt.where(Document.status != DocumentStatus.deleted)
        if status_filter is not None:
            stmt = stmt.where(Document.status == status_filter)
        stmt = stmt.order_by(Document.created_at.desc()).offset(offset).limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count_by_kb(self, kb_id: str, *, active_only: bool = True) -> int:
        stmt = select(func.count(Document.id)).where(
            Document.knowledge_base_id == kb_id
        )
        if active_only:
            stmt = stmt.where(Document.status != DocumentStatus.deleted)
        result = await self._session.execute(stmt)
        return result.scalar_one()

    async def find_by_hash(self, kb_id: str, file_hash: str) -> Document | None:
        stmt = select(Document).where(
            Document.knowledge_base_id == kb_id,
            Document.file_hash == file_hash,
            Document.status != DocumentStatus.deleted,
        ).order_by(Document.created_at.desc())
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def update(self, doc_id: str, **values: Any) -> Document | None:
        doc = await self.get(doc_id)
        if doc is None:
            return None
        for key, val in values.items():
            if hasattr(doc, key):
                setattr(doc, key, val)
        doc.updated_at = datetime.now(tz=UTC)
        await self._session.flush()
        return doc

    async def mark_parsed_fenced(
        self,
        doc_id: str,
        *,
        job_id: str,
        knowledge_base_id: str,
        worker_id: str,
        lease_token: str,
        fencing_token: int,
        attempt: int,
        page_count: int,
        parent_chunk_count: int,
        child_chunk_count: int,
        now: datetime,
    ) -> bool:
        """Persist parse metadata only while the registering claimant remains current."""
        current_job = exists(
            select(UpdateJob.id).where(
                UpdateJob.id == job_id,
                UpdateJob.knowledge_base_id == knowledge_base_id,
                UpdateJob.worker_id == worker_id,
                UpdateJob.lease_token == lease_token,
                UpdateJob.fencing_token == fencing_token,
                UpdateJob.attempt == attempt,
                UpdateJob.lease_expires_at > now,
            )
        )
        current_lease = exists(
            select(KBOperationLease.knowledge_base_id).where(
                KBOperationLease.knowledge_base_id == knowledge_base_id,
                KBOperationLease.lock_owner == worker_id,
                KBOperationLease.lease_token == lease_token,
                KBOperationLease.fencing_token == fencing_token,
                KBOperationLease.expires_at > now,
            )
        )
        statement = (
            update(Document)
            .where(
                Document.id == doc_id,
                Document.knowledge_base_id == knowledge_base_id,
                current_job,
                current_lease,
            )
            .values(
                page_count=page_count,
                parent_chunk_count=parent_chunk_count,
                child_chunk_count=child_chunk_count,
                parse_status="done",
                status=DocumentStatus.parsed,
                parser_version="1.28.0",
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        result = await self._session.execute(statement)
        await self._session.commit()
        return result.rowcount == 1

    async def soft_delete(self, doc_id: str) -> Document | None:
        doc = await self.get(doc_id)
        if doc is None:
            return None
        doc.status = DocumentStatus.deleting
        doc.is_active = False
        doc.deleted_at = datetime.now(tz=UTC)
        doc.updated_at = datetime.now(tz=UTC)
        await self._session.flush()
        return doc

    async def mark_deleted(self, doc_id: str) -> Document | None:
        doc = await self.get(doc_id)
        if doc is None:
            return None
        doc.status = DocumentStatus.deleted
        doc.updated_at = datetime.now(tz=UTC)
        await self._session.flush()
        return doc

    async def list_active_for_kb(self, kb_id: str) -> list[Document]:
        stmt = select(Document).where(
            Document.knowledge_base_id == kb_id,
            Document.is_active == True,  # noqa: E712
            Document.status != DocumentStatus.deleted,
        ).order_by(Document.created_at.asc())
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
