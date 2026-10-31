import math
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.database.db import chunk_dict, connect, row_dict
from app.services.vector_store import counts_by_document, get_embedding, indexed_ids

router = APIRouter(prefix="/chunks", tags=["chunks"])


@router.get("")
def list_chunks(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    document_id: str | None = None,
    file_type: str | None = None,
    search: str | None = None,
) -> dict[str, Any]:
    filters, values = [], []
    if document_id:
        filters.append("c.document_id = ?")
        values.append(document_id)
    if file_type:
        filters.append("d.file_type = ?")
        values.append(file_type.lower().lstrip("."))
    if search:
        filters.append("c.text LIKE ?")
        values.append(f"%{search}%")
    where = f"WHERE {' AND '.join(filters)}" if filters else ""
    query = f"""FROM chunks c JOIN documents d ON d.id = c.document_id {where}"""
    try:
        indexed_chunk_total, indexed_counts = counts_by_document()
    except Exception as error:
        raise HTTPException(status_code=503, detail="Could not read indexed chunks from the vector store.") from error
    with connect() as connection:
        total = connection.execute(f"SELECT COUNT(*) {query}", values).fetchone()[0]
        rows = connection.execute(
            f"""SELECT c.*, d.filename, d.file_type {query}
                ORDER BY d.uploaded_at DESC, c.chunk_index LIMIT ? OFFSET ?""",
            [*values, page_size, (page - 1) * page_size],
        ).fetchall()
        document_ids = {
            row["id"] for row in connection.execute("SELECT id FROM documents").fetchall()
        }
    indexed_document_total = sum(indexed_counts.get(document_id, 0) for document_id in document_ids)
    actual_indexed_ids = indexed_ids([row["id"] for row in rows])
    items = []
    for row in rows:
        item = chunk_dict(row)
        assert item is not None
        if row["id"] in actual_indexed_ids:
            item["indexing_status"] = "indexed"
        elif item["indexing_status"] == "indexed":
            item["indexing_status"] = "missing"
        items.append(item)
    return {
        "items": items, "total": total, "page": page,
        "page_size": page_size, "pages": math.ceil(total / page_size),
        "indexed_chunk_total": indexed_chunk_total,
        "unassociated_indexed_chunk_count": indexed_chunk_total - indexed_document_total,
    }


@router.get("/{chunk_id}")
def get_chunk(chunk_id: str) -> dict[str, Any]:
    with connect() as connection:
        row = connection.execute(
            """SELECT c.*, d.filename, d.file_type FROM chunks c
               JOIN documents d ON d.id = c.document_id WHERE c.id = ?""",
            (chunk_id,),
        ).fetchone()
    chunk = chunk_dict(row)
    if chunk is None:
        raise HTTPException(status_code=404, detail="Chunk not found.")
    return chunk


@router.get("/{chunk_id}/embedding")
def get_chunk_embedding(chunk_id: str, full: bool = False) -> dict[str, Any]:
    with connect() as connection:
        row = connection.execute(
            """SELECT c.id, c.document_id, c.embedding_status, c.indexing_status,
                      c.embedding_dimensions, c.embedding_model, d.filename, d.file_type
               FROM chunks c JOIN documents d ON d.id = c.document_id WHERE c.id = ?""",
            (chunk_id,),
        ).fetchone()
    chunk = row_dict(row)
    if chunk is None:
        raise HTTPException(status_code=404, detail="Chunk not found.")
    if chunk["embedding_status"] != "complete" or chunk["indexing_status"] != "indexed":
        raise HTTPException(status_code=409, detail="This chunk does not have a completed embedding.")
    result = get_embedding(chunk_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Embedding vector was not found in the vector store.")
    vector, _ = result
    payload = {
        **chunk,
        "dimensions": len(vector),
        "preview": vector if full else vector[:20],
        "full": full,
    }
    if full:
        payload["vector"] = vector
    return payload
