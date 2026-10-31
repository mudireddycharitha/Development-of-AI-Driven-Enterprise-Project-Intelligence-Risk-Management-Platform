import logging

from fastapi import APIRouter

from app.core.config import settings
from app.database.db import connect
from app.services.document_parser import SUPPORTED_FORMATS
from app.services.vector_store import counts_by_document, count

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/health")
def health() -> dict:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with connect() as connection:
        connection.execute("SELECT 1").fetchone()
    try:
        indexed_vectors = count()
        vector_store = "healthy"
    except Exception:
        logger.exception("Vector-store health check failed")
        indexed_vectors = None
        vector_store = "unavailable"
    return {
        "status": "healthy" if vector_store == "healthy" else "degraded",
        "database": "healthy",
        "vector_store": vector_store,
        "indexed_vectors": indexed_vectors,
        "embedding_model": settings.embedding_model,
    }


@router.get("/supported-formats")
def supported_formats() -> dict:
    return {"formats": SUPPORTED_FORMATS}


@router.get("/stats")
def stats() -> dict:
    with connect() as connection:
        documents = connection.execute(
            """SELECT COUNT(*) AS total,
               SUM(CASE WHEN processing_status = 'indexed' THEN 1 ELSE 0 END) AS successful,
               SUM(CASE WHEN processing_status = 'failed' THEN 1 ELSE 0 END) AS failed,
               SUM(CASE WHEN processing_status IN ('uploaded','validating','extracting','chunking','embedding')
                        THEN 1 ELSE 0 END) AS processing,
               (SELECT COUNT(*) FROM chunks) AS chunks
               FROM documents"""
        ).fetchone()
        recent = connection.execute(
            """SELECT id, filename, file_type, file_size, uploaded_at, processing_status,
                      (SELECT COUNT(*) FROM chunks c WHERE c.document_id = documents.id) AS chunk_count
               FROM documents ORDER BY uploaded_at DESC LIMIT 8"""
        ).fetchall()
    try:
        vector_health = "healthy"
        vectors, indexed_counts = counts_by_document()
    except Exception:
        logger.exception("Vector-store statistics check failed")
        vector_health = "unavailable"
        vectors = None
        indexed_counts = {}
    recent_documents = []
    for row in recent:
        document = dict(row)
        document["indexed_chunk_count"] = indexed_counts.get(document["id"]) if vectors is not None else None
        recent_documents.append(document)
    return {
        "total_documents": documents["total"],
        "successful_documents": documents["successful"] or 0,
        "failed_documents": documents["failed"] or 0,
        "processing_documents": documents["processing"] or 0,
        "total_chunks": documents["chunks"] or 0,
        "indexed_chunks": vectors,
        "recent_documents": recent_documents,
        "supported_formats": list(SUPPORTED_FORMATS),
        "backend_status": "healthy",
        "vector_store_status": vector_health,
        "vector_count": vectors,
    }
