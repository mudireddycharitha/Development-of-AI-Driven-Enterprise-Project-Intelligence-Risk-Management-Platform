import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.database.db import connect, row_dict
from app.services.chunking import TextSegment, chunk_segments
from app.services.document_parser import extract_segments
from app.services.embedding_service import embed_texts, model_name
from app.services.vector_store import delete_document, upsert_chunks

logger = logging.getLogger(__name__)


def safe_error_message(error: Exception) -> str:
    message = str(error).strip()
    message = re.sub(r"""(?i)['"](?:[a-z]:\\|\\\\)[^'"]*['"]""", "'<local path>'", message)
    message = re.sub(r"""(?i)(?:[a-z]:\\|\\\\)[^\s"'<>,;]+""", "<local path>", message)
    message = re.sub(r"""(?<![:\w])/[^ \t\r\n"'<>]+""", "<local path>", message)
    return (message or "Processing failed unexpectedly.")[:1000]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def update_status(document_id: str, status: str, error: str | None = None, stage: str | None = None) -> None:
    with connect() as connection:
        connection.execute(
            """UPDATE documents SET processing_status = ?, extraction_error = ?,
               failed_stage = ?, updated_at = ? WHERE id = ?""",
            (status, error, stage, utc_now(), document_id),
        )


def process_document(document_id: str) -> dict[str, Any]:
    with connect() as connection:
        document = row_dict(connection.execute(
            "SELECT * FROM documents WHERE id = ?", (document_id,)
        ).fetchone())
    if document is None:
        raise LookupError("Document not found.")
    stored_path = (settings.uploads_dir / document["stored_filename"]).resolve()
    if stored_path.parent != settings.uploads_dir.resolve() or not stored_path.is_file():
        update_status(document_id, "failed", "Stored upload is missing or inaccessible.", "validation")
        raise FileNotFoundError("Stored upload is missing or inaccessible.")

    try:
        update_status(document_id, "extracting")
        segments = extract_segments(stored_path)
        extracted_text = "\n\n".join(segment.text for segment in segments)
        if len(extracted_text) > settings.max_extracted_chars:
            raise ValueError(
                f"Extracted content exceeds the {settings.max_extracted_chars:,} character processing limit."
            )
        with connect() as connection:
            connection.execute(
                "UPDATE documents SET extracted_text = ?, updated_at = ? WHERE id = ?",
                (extracted_text, utc_now(), document_id),
            )

        update_status(document_id, "chunking")
        delete_document(document_id)
        with connect() as connection:
            connection.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))
        chunk_size = settings.chunk_size
        overlap = settings.chunk_overlap
        if overlap >= chunk_size:
            raise ValueError("Chunk overlap must be smaller than chunk size.")
        chunk_results = chunk_segments(segments, chunk_size, overlap)
        if not chunk_results:
            raise ValueError("No nonempty chunks were created.")
        if len(chunk_results) > settings.max_chunks_per_document:
            raise ValueError(
                f"Document exceeds the {settings.max_chunks_per_document:,} chunk processing limit."
            )

        file_type = Path(document["filename"]).suffix.lower().lstrip(".")
        records: list[dict[str, Any]] = []
        for index, chunk in enumerate(chunk_results):
            chunk_id = f"{document_id}_{index:06d}"
            location = chunk["location"]
            vector_metadata: dict[str, str | int | float | bool] = {
                "document_id": document_id,
                "filename": document["filename"],
                "file_type": file_type,
                "chunk_index": index,
            }
            for key, value in location.items():
                if isinstance(value, (str, int, float, bool)):
                    vector_metadata[key] = value
            records.append({
                "id": chunk_id,
                "document_id": document_id,
                "chunk_index": index,
                "text": chunk["text"],
                "token_count": chunk["token_count"],
                "location_json": json.dumps(location, ensure_ascii=False),
                "location": location,
                "vector_metadata": vector_metadata,
            })
        with connect() as connection:
            connection.executemany(
                """INSERT INTO chunks (id, document_id, chunk_index, text, token_count,
                   location_json, embedding_status, indexing_status)
                   VALUES (:id, :document_id, :chunk_index, :text, :token_count,
                   :location_json, 'pending', 'pending')""",
                records,
            )
            connection.execute(
                """UPDATE documents SET chunk_count = ?, indexed_chunk_count = 0,
                   updated_at = ? WHERE id = ?""",
                (len(records), utc_now(), document_id),
            )

        update_status(document_id, "embedding")
        vectors = embed_texts([record["text"] for record in records])
        dimensions = len(vectors[0])
        if any(len(vector) != dimensions for vector in vectors):
            raise RuntimeError("Embedding vectors have inconsistent dimensions.")
        with connect() as connection:
            connection.executemany(
                """UPDATE chunks SET embedding_status = 'complete', embedding_dimensions = ?,
                   embedding_model = ? WHERE id = ?""",
                [(dimensions, model_name(), record["id"]) for record in records],
            )
        upsert_chunks(records, vectors)
        with connect() as connection:
            connection.execute(
                """UPDATE chunks SET indexing_status = 'indexed' WHERE document_id = ?""",
                (document_id,),
            )
            connection.execute(
                """UPDATE documents SET processing_status = 'indexed', extraction_error = NULL,
                   failed_stage = NULL, indexed_chunk_count = ?, updated_at = ? WHERE id = ?""",
                (len(records), utc_now(), document_id),
            )
            result = row_dict(connection.execute(
                "SELECT * FROM documents WHERE id = ?", (document_id,)
            ).fetchone())
        return result or {}
    except Exception as error:
        stage = "processing"
        with connect() as connection:
            row = connection.execute(
                "SELECT processing_status FROM documents WHERE id = ?", (document_id,)
            ).fetchone()
            if row:
                stage = row["processing_status"]
        logger.exception("Document %s failed during %s", document_id, stage)
        try:
            with connect() as connection:
                connection.execute(
                    """UPDATE chunks SET embedding_status = 'failed', indexing_status = 'failed'
                       WHERE document_id = ? AND indexing_status != 'indexed'""",
                    (document_id,),
                )
                connection.execute(
                    """UPDATE documents SET processing_status = 'failed', extraction_error = ?,
                       failed_stage = ?, updated_at = ? WHERE id = ?""",
                    (safe_error_message(error), stage, utc_now(), document_id),
                )
        except Exception:
            logger.exception("Unable to record failure for document %s", document_id)
        raise
