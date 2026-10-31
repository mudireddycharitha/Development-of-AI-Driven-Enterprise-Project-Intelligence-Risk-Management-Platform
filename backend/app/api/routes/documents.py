import hashlib
import logging
import math
import re
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.database.db import connect, row_dict
from app.services.document_parser import SUPPORTED_FORMATS
from app.services.pipeline import process_document, safe_error_message, utc_now, update_status
from app.services.vector_store import counts_by_document, delete_document

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/documents", tags=["documents"])
SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


def _file_type(filename: str) -> str:
    return Path(filename).suffix.lower().lstrip(".")


@router.post("/upload")
async def upload_documents(files: list[UploadFile] = File(...)) -> dict[str, Any]:
    if not files:
        raise HTTPException(status_code=400, detail="Select at least one file.")
    if len(files) > settings.max_batch_files:
        raise HTTPException(
            status_code=413,
            detail=f"A batch may contain at most {settings.max_batch_files} files.",
        )
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    for upload in files:
        original_name = Path(upload.filename or "unnamed").name
        extension = Path(original_name).suffix.lower()
        if extension not in SUPPORTED_FORMATS:
            results.append({
                "filename": original_name, "status": "failed",
                "error": f"Unsupported file format: {extension or '(no extension)'}",
            })
            await upload.close()
            continue
        if not original_name or original_name in {".", ".."}:
            results.append({"filename": original_name, "status": "failed", "error": "Invalid filename."})
            await upload.close()
            continue

        document_id = str(uuid.uuid4())
        stored_filename = f"{document_id}{extension}"
        destination = settings.uploads_dir / stored_filename
        digest = hashlib.sha256()
        file_size = 0
        try:
            with destination.open("xb") as output:
                while content := await upload.read(1024 * 1024):
                    file_size += len(content)
                    if file_size > settings.max_file_size:
                        raise ValueError(
                            f"File exceeds the {settings.max_file_size // (1024 * 1024)} MB size limit."
                        )
                    digest.update(content)
                    output.write(content)
            if file_size == 0:
                raise ValueError("The uploaded file is empty.")
            file_digest = digest.hexdigest()
            with connect() as connection:
                existing = row_dict(connection.execute(
                    """SELECT id, processing_status, chunk_count, indexed_chunk_count
                       FROM documents WHERE sha256 = ?""",
                    (file_digest,),
                ).fetchone())
            if existing:
                destination.unlink(missing_ok=True)
                results.append({
                    "document_id": existing["id"],
                    "filename": original_name,
                    "status": existing["processing_status"],
                    "chunk_count": existing["chunk_count"],
                    "indexed_chunk_count": existing["indexed_chunk_count"],
                    "duplicate": True,
                })
                continue
            name = SAFE_FILENAME.sub("_", original_name).strip("._") or f"document{extension}"
            stored_name = f"{document_id}_{name}"
            renamed = settings.uploads_dir / stored_name
            destination.replace(renamed)
            destination = renamed
            now = utc_now()
            try:
                with connect() as connection:
                    connection.execute(
                        """INSERT INTO documents
                           (id, filename, stored_filename, file_type, file_size, sha256,
                            uploaded_at, created_at, updated_at, processing_status)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'uploaded')""",
                        (document_id, original_name, stored_name, _file_type(original_name),
                         file_size, file_digest, now, now, now),
                    )
            except sqlite3.IntegrityError:
                with connect() as connection:
                    existing = row_dict(connection.execute(
                        """SELECT id, processing_status, chunk_count, indexed_chunk_count
                           FROM documents WHERE sha256 = ?""",
                        (file_digest,),
                    ).fetchone())
                if existing is None:
                    raise
                destination.unlink(missing_ok=True)
                results.append({
                    "document_id": existing["id"], "filename": original_name,
                    "status": existing["processing_status"], "chunk_count": existing["chunk_count"],
                    "indexed_chunk_count": existing["indexed_chunk_count"], "duplicate": True,
                })
                continue
            try:
                document = await run_in_threadpool(process_document, document_id)
                results.append({
                    "document_id": document_id,
                    "filename": original_name,
                    "status": document["processing_status"],
                    "chunk_count": document["chunk_count"],
                    "indexed_chunk_count": document["indexed_chunk_count"],
                })
            except Exception as error:
                logger.exception("Upload processing failed for %s", original_name)
                with connect() as connection:
                    failed = row_dict(connection.execute(
                        "SELECT * FROM documents WHERE id = ?", (document_id,)
                    ).fetchone())
                results.append({
                    "document_id": document_id,
                    "filename": original_name,
                    "status": "failed",
                    "error": (failed or {}).get("extraction_error") or safe_error_message(error),
                })
        except ValueError as error:
            destination.unlink(missing_ok=True)
            results.append({"filename": original_name, "status": "failed", "error": str(error)})
        except Exception as error:
            destination.unlink(missing_ok=True)
            logger.exception("Could not store upload %s", original_name)
            results.append({
                "filename": original_name, "status": "failed",
                "error": "The file could not be stored or registered.",
            })
        finally:
            await upload.close()
    return {
        "results": results,
        "successful": sum(result["status"] == "indexed" for result in results),
        "failed": sum(result["status"] == "failed" for result in results),
    }


@router.get("")
def list_documents(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = None,
    file_type: str | None = None,
    search: str | None = None,
) -> dict[str, Any]:
    filters, values = [], []
    if status:
        filters.append("processing_status = ?")
        values.append(status)
    if file_type:
        filters.append("file_type = ?")
        values.append(file_type.lower().lstrip("."))
    if search:
        filters.append("filename LIKE ?")
        values.append(f"%{search}%")
    where = f"WHERE {' AND '.join(filters)}" if filters else ""
    try:
        indexed_chunk_total, indexed_counts = counts_by_document()
    except Exception as error:
        logger.exception("Could not count indexed vectors")
        raise HTTPException(status_code=503, detail="Could not read indexed chunks from the vector store.") from error
    with connect() as connection:
        total = connection.execute(f"SELECT COUNT(*) FROM documents {where}", values).fetchone()[0]
        rows = connection.execute(
            f"""SELECT id, filename, file_type, file_size, uploaded_at, created_at, updated_at,
                       processing_status, extraction_error, failed_stage,
                       (SELECT COUNT(*) FROM chunks c WHERE c.document_id = documents.id) AS chunk_count
                FROM documents {where} ORDER BY uploaded_at DESC LIMIT ? OFFSET ?""",
            [*values, page_size, (page - 1) * page_size],
        ).fetchall()
        document_ids = {
            row["id"] for row in connection.execute("SELECT id FROM documents").fetchall()
        }
    indexed_document_total = sum(indexed_counts.get(document_id, 0) for document_id in document_ids)
    return {
        "items": [
            {
                **dict(row),
                "indexed_chunk_count": indexed_counts.get(row["id"], 0),
            }
            for row in rows
        ],
        "total": total, "page": page,
        "page_size": page_size, "pages": math.ceil(total / page_size),
        "indexed_chunk_total": indexed_chunk_total,
        "unassociated_indexed_chunk_count": indexed_chunk_total - indexed_document_total,
    }


@router.get("/{document_id}")
def get_document(document_id: str) -> dict[str, Any]:
    with connect() as connection:
        document = row_dict(connection.execute(
            """SELECT documents.*,
                      (SELECT COUNT(*) FROM chunks c WHERE c.document_id = documents.id) AS current_chunk_count
               FROM documents WHERE id = ?""",
            (document_id,),
        ).fetchone())
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    try:
        _, indexed_counts = counts_by_document()
    except Exception as error:
        logger.exception("Could not count indexed vectors")
        raise HTTPException(status_code=503, detail="Could not read indexed chunks from the vector store.") from error
    document["chunk_count"] = document.pop("current_chunk_count")
    document["indexed_chunk_count"] = indexed_counts.get(document_id, 0)
    document.pop("stored_filename", None)
    document.pop("sha256", None)
    return document


@router.get("/{document_id}/text")
def get_document_text(document_id: str) -> dict[str, str]:
    with connect() as connection:
        row = connection.execute(
            "SELECT filename, extracted_text FROM documents WHERE id = ?", (document_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    return {"document_id": document_id, "filename": row["filename"], "text": row["extracted_text"]}


@router.get("/{document_id}/chunks")
def document_chunks(
    document_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    with connect() as connection:
        exists = connection.execute("SELECT 1 FROM documents WHERE id = ?", (document_id,)).fetchone()
        total = connection.execute(
            "SELECT COUNT(*) FROM chunks WHERE document_id = ?", (document_id,)
        ).fetchone()[0]
        rows = connection.execute(
            """SELECT c.*, d.filename, d.file_type FROM chunks c JOIN documents d ON d.id = c.document_id
               WHERE c.document_id = ? ORDER BY c.chunk_index LIMIT ? OFFSET ?""",
            (document_id, page_size, (page - 1) * page_size),
        ).fetchall()
    if exists is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    from app.database.db import chunk_dict
    return {
        "items": [chunk_dict(row) for row in rows],
        "total": total, "page": page, "page_size": page_size,
        "pages": math.ceil(total / page_size),
    }


@router.post("/{document_id}/reprocess")
def reprocess_document(document_id: str) -> dict[str, Any]:
    with connect() as connection:
        exists = connection.execute("SELECT 1 FROM documents WHERE id = ?", (document_id,)).fetchone()
    if exists is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    update_status(document_id, "validating")
    try:
        document = process_document(document_id)
        document.pop("stored_filename", None)
        document.pop("sha256", None)
        return document
    except Exception as error:
        with connect() as connection:
            row = connection.execute(
                "SELECT extraction_error, failed_stage FROM documents WHERE id = ?", (document_id,)
            ).fetchone()
        raise HTTPException(
            status_code=422,
            detail={"message": row["extraction_error"] if row else safe_error_message(error),
                    "stage": row["failed_stage"] if row else "processing"},
        ) from error


@router.delete("/{document_id}")
def delete_document_endpoint(document_id: str) -> dict[str, str]:
    with connect() as connection:
        document = row_dict(connection.execute(
            "SELECT stored_filename FROM documents WHERE id = ?", (document_id,)
        ).fetchone())
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    try:
        delete_document(document_id)
    except Exception as error:
        logger.exception("Could not remove vectors for document %s", document_id)
        raise HTTPException(status_code=503, detail="Could not clean up the document index.") from error
    with connect() as connection:
        connection.execute("DELETE FROM documents WHERE id = ?", (document_id,))
    (settings.uploads_dir / document["stored_filename"]).unlink(missing_ok=True)
    return {"document_id": document_id, "status": "deleted"}
