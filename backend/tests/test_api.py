from fastapi.testclient import TestClient

from app.main import app
from app.api.routes import search
from app.database.db import connect
from app.services import pipeline, vector_store


def test_mixed_upload_search_persistence_retry_and_delete(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "platform-data"))
    monkeypatch.setenv("CHUNK_SIZE_TOKENS", "20")
    monkeypatch.setenv("CHUNK_OVERLAP_TOKENS", "2")
    monkeypatch.setattr(pipeline, "embed_texts", lambda texts: [[1.0, 0.0, 0.0] for _ in texts])
    monkeypatch.setattr(search, "embed_texts", lambda texts: [[1.0, 0.0, 0.0] for _ in texts])
    vector_store.reset_client()

    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["database"] == "healthy"

        response = client.post("/documents/upload", files=[
            ("files", ("risk.txt", b"Project Alpha faces a schedule risk and needs a mitigation owner.", "text/plain")),
            ("files", ("portfolio.csv", b"Project,Status\nAlpha,Delayed\n", "text/csv")),
            ("files", ("risk-copy.txt", b"Project Alpha faces a schedule risk and needs a mitigation owner.", "text/plain")),
            ("files", ("empty.txt", b"", "text/plain")),
            ("files", ("archive.exe", b"not allowed", "application/octet-stream")),
        ])
        assert response.status_code == 200
        result = response.json()["results"]
        assert [item["status"] for item in result] == ["indexed", "indexed", "indexed", "failed", "failed"]
        assert result[2]["duplicate"] is True
        assert result[2]["document_id"] == result[0]["document_id"]
        assert "empty" in result[3]["error"].lower()
        assert "unsupported" in result[4]["error"].lower()
        first_id = result[0]["document_id"]

        document_page = client.get("/documents", params={"page": 1, "page_size": 1}).json()
        assert document_page["total"] == 2 and document_page["pages"] == 2
        document = client.get(f"/documents/{first_id}").json()
        assert document["processing_status"] == "indexed"
        assert document["chunk_count"] > 0
        assert "schedule risk" in client.get(f"/documents/{first_id}/text").json()["text"]

        chunks = client.get("/chunks", params={"document_id": first_id}).json()
        assert chunks["total"] > 0
        filtered_chunks = client.get("/chunks", params={"file_type": "csv", "search": "Delayed", "page_size": 1}).json()
        assert filtered_chunks["total"] > 0 and filtered_chunks["items"][0]["file_type"] == "csv"
        chunk = chunks["items"][0]
        embedding = client.get(f"/chunks/{chunk['id']}/embedding").json()
        assert embedding["dimensions"] == 3
        assert len(embedding["preview"]) == 3

        extracted_count = result[0]["chunk_count"]
        with connect() as connection:
            connection.execute(
                """INSERT INTO chunks (id, document_id, chunk_index, text, token_count)
                   VALUES (?, ?, ?, ?, ?)""",
                (f"{first_id}_pending", first_id, extracted_count, "pending chunk", 2),
            )
            connection.execute(
                "UPDATE documents SET chunk_count = 999, indexed_chunk_count = 999 WHERE id = ?",
                (first_id,),
            )
            connection.execute(
                "UPDATE chunks SET indexing_status = 'indexed' WHERE id = ?",
                (f"{first_id}_pending",),
            )
        vector_store.get_collection().upsert(
            ids=["orphan_vector"],
            documents=["orphaned vector"],
            embeddings=[[0.0, 1.0, 0.0]],
            metadatas=[{"document_id": "deleted-document"}],
        )
        document_summary = client.get("/documents", params={"page_size": 100}).json()
        document_counts = {item["id"]: item for item in document_summary["items"]}
        assert document_counts[first_id]["chunk_count"] == extracted_count + 1
        assert document_counts[first_id]["indexed_chunk_count"] == extracted_count
        assert sum(item["indexed_chunk_count"] for item in document_counts.values()) == vector_store.count() - 1
        assert document_summary["indexed_chunk_total"] == vector_store.count()
        assert document_summary["unassociated_indexed_chunk_count"] == 1
        document_detail = client.get(f"/documents/{first_id}").json()
        assert document_detail["chunk_count"] == extracted_count + 1
        assert document_detail["indexed_chunk_count"] == extracted_count

        chunk_summary = client.get("/chunks", params={"page_size": 100}).json()
        assert chunk_summary["total"] == extracted_count + result[1]["chunk_count"] + 1
        assert chunk_summary["indexed_chunk_total"] == document_summary["indexed_chunk_total"]
        assert chunk_summary["unassociated_indexed_chunk_count"] == 1
        stale_chunk = next(item for item in chunk_summary["items"] if item["id"] == f"{first_id}_pending")
        assert stale_chunk["indexing_status"] == "missing"
        vector_store.get_collection().delete(ids=["orphan_vector"])

        results = client.post("/search", json={"query": "Which delivery risks are present?", "top_k": 5}).json()["results"]
        risk_result = next((item for item in results if item["filename"] == "risk.txt"), None)
        assert risk_result is not None
        assert "schedule risk" in risk_result["text"]
        assert "distance" in risk_result
        csv_results = client.post(
            "/search",
            json={"query": "project delivery risk mitigation", "top_k": 5, "file_type": "csv"},
        ).json()["results"]
        assert csv_results and all(item["file_type"] == "csv" for item in csv_results)

        vector_store.reset_client()
        expected_vectors = result[0]["chunk_count"] + result[1]["chunk_count"]
        assert client.get("/health").json()["indexed_vectors"] == expected_vectors
        indexed_total, document_vector_counts = vector_store.counts_by_document(batch_size=1)
        assert indexed_total == expected_vectors
        assert document_vector_counts == {
            result[0]["document_id"]: result[0]["chunk_count"],
            result[1]["document_id"]: result[1]["chunk_count"],
        }
        stats = client.get("/stats").json()
        assert stats["indexed_chunks"] == stats["vector_count"] == expected_vectors

        retry = client.post(f"/documents/{first_id}/reprocess")
        assert retry.status_code == 200
        assert retry.json()["processing_status"] == "indexed"
        assert client.delete(f"/documents/{first_id}").status_code == 200
        assert client.get(f"/documents/{first_id}").status_code == 404
        assert client.get("/stats").json()["total_documents"] == 1
        remaining = client.get("/documents").json()["items"][0]
        assert client.get("/health").json()["indexed_vectors"] == remaining["indexed_chunk_count"]
    vector_store.reset_client()


def test_document_pagination_and_empty_search_validation(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "platform-data"))
    vector_store.reset_client()
    with TestClient(app) as client:
        response = client.get("/documents", params={"page": 1, "page_size": 2})
        assert response.status_code == 200
        assert response.json()["page"] == 1
        assert client.post("/search", json={"query": "   ", "top_k": 5}).status_code == 422
    vector_store.reset_client()


def test_search_reports_missing_embedding_dependency(monkeypatch) -> None:
    def missing_model(_: list[str]) -> list[list[float]]:
        raise ModuleNotFoundError("No module named 'sentence_transformers'", name="sentence_transformers")

    monkeypatch.setattr(search, "embed_texts", missing_model)
    with TestClient(app) as client:
        response = client.post("/search", json={"query": "project schedule risks"})
    assert response.status_code == 503
    assert "sentence-transformers/all-MiniLM-L6-v2" in response.json()["detail"]
    assert "sentence_transformers" in response.json()["detail"]
    assert "Install backend requirements" in response.json()["detail"]


def test_search_reports_vector_store_failure(monkeypatch) -> None:
    monkeypatch.setattr(search, "embed_texts", lambda _: [[0.1, 0.2]])

    def unavailable_vector_store(*args, **kwargs):
        raise RuntimeError("collection unavailable")

    monkeypatch.setattr(search, "query", unavailable_vector_store)
    with TestClient(app) as client:
        response = client.post("/search", json={"query": "project schedule risks"})
    assert response.status_code == 503
    assert "ChromaDB" in response.json()["detail"]
    assert "vector-store health" in response.json()["detail"]
