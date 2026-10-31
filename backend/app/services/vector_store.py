from pathlib import Path
from typing import Any

import chromadb

from app.core.config import settings

_client: Any = None
_collection: Any = None
_client_path: Path | None = None


def get_collection() -> Any:
    global _client, _collection, _client_path
    path = settings.chroma_dir
    path.mkdir(parents=True, exist_ok=True)
    if _collection is None or _client_path != path:
        _client = chromadb.PersistentClient(path=str(path))
        _collection = _client.get_or_create_collection(
            name="document_chunks",
            metadata={"hnsw:space": "cosine"},
        )
        _client_path = path
    return _collection


def reset_client() -> None:
    global _client, _collection, _client_path
    _client = None
    _collection = None
    _client_path = None


def upsert_chunks(chunks: list[dict[str, Any]], vectors: list[list[float]]) -> None:
    if not chunks or len(chunks) != len(vectors):
        raise ValueError("Chunk and embedding counts must match and be nonempty.")
    collection = get_collection()
    collection.upsert(
        ids=[chunk["id"] for chunk in chunks],
        documents=[chunk["text"] for chunk in chunks],
        embeddings=vectors,
        metadatas=[chunk["vector_metadata"] for chunk in chunks],
    )


def delete_document(document_id: str) -> None:
    collection = get_collection()
    collection.delete(where={"document_id": document_id})


def count() -> int:
    return int(get_collection().count())


def counts_by_document(batch_size: int = 1000) -> tuple[int, dict[str, int]]:
    if batch_size < 1:
        raise ValueError("Vector-store metadata batch size must be positive.")
    collection = get_collection()
    document_counts: dict[str, int] = {}
    total = 0
    offset = 0
    while True:
        result = collection.get(
            limit=batch_size,
            offset=offset,
            include=["metadatas"],
        )
        ids = result["ids"]
        if not ids:
            break
        total += len(ids)
        for metadata in result["metadatas"]:
            document_id = (metadata or {}).get("document_id")
            if isinstance(document_id, str) and document_id:
                document_counts[document_id] = document_counts.get(document_id, 0) + 1
        offset += len(ids)
    return total, document_counts


def indexed_ids(chunk_ids: list[str]) -> set[str]:
    if not chunk_ids:
        return set()
    return set(get_collection().get(ids=chunk_ids, include=["metadatas"])["ids"])


def get_embedding(chunk_id: str) -> tuple[list[float], dict[str, Any]] | None:
    result = get_collection().get(
        ids=[chunk_id], include=["embeddings", "metadatas"]
    )
    if not result["ids"]:
        return None
    vector = result["embeddings"][0]
    return [float(value) for value in vector], result["metadatas"][0] or {}


def query(
    vector: list[float], top_k: int, document_id: str | None = None, file_type: str | None = None
) -> list[dict[str, Any]]:
    conditions = []
    if document_id:
        conditions.append({"document_id": document_id})
    if file_type:
        conditions.append({"file_type": file_type.lower().lstrip(".")})
    where = conditions[0] if len(conditions) == 1 else ({"$and": conditions} if conditions else None)
    collection = get_collection()
    if collection.count() == 0:
        return []
    result = collection.query(
        query_embeddings=[vector],
        n_results=min(top_k, collection.count()),
        where=where,
        include=["documents", "metadatas", "distances"],
    )
    found: list[dict[str, Any]] = []
    for index, chunk_id in enumerate(result["ids"][0]):
        found.append({
            "id": chunk_id,
            "text": result["documents"][0][index],
            "metadata": result["metadatas"][0][index] or {},
            "distance": float(result["distances"][0][index]),
        })
    return found
