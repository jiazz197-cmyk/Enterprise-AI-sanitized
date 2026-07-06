"""Adapters for document processing (integrations + executor)."""

from __future__ import annotations

from typing import Any, List

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.executor import attach_future_result_logger, executor_manager
from app.integrations.doc_processing.document_task_runner import (
    process_documents_background,
    upload_and_register_documents,
)
from app.ports.outbound.document_processing import DocumentProcessWorkerPort, DocumentRegistrationPort


class SqlAlchemyDocumentRegistrationAdapter(DocumentRegistrationPort):
    def __init__(self, db: AsyncSession):
        self._db = db

    async def register_uploaded_files(self, files: Any, normalized_uploader: str) -> List[int]:
        return await upload_and_register_documents(self._db, files, normalized_uploader)


class DocumentProcessWorkerAdapter(DocumentProcessWorkerPort):
    def submit_process_documents(
        self,
        task_id: str,
        file_ids: List[int],
        instance_id: int,
        chunk_size: int,
        chunk_overlap: int,
    ) -> None:
        executor_manager.submit_task(
            task_id,
            process_documents_background,
            task_id,
            file_ids,
            instance_id,
            chunk_size,
            chunk_overlap,
        )
        future = executor_manager.get_task_future(task_id)
        if future is not None:
            attach_future_result_logger(future, task_id)
