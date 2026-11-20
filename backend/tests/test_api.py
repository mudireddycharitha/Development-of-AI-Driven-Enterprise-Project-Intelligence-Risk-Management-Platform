from fastapi.testclient import TestClient

from app.main import app
from app.api.routes import search
from app.database.db import connect
from app.services import assistant, pipeline, vector_store


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


def test_updated_document_versions_replace_current_retrieval_without_losing_history(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "platform-data"))
    embedding_batches = []

    def fake_embed(texts):
        embedding_batches.append(list(texts))
        return [[1.0, 0.0, 0.0] for _ in texts]

    monkeypatch.setattr(pipeline, "embed_texts", fake_embed)
    monkeypatch.setattr(search, "embed_texts", lambda texts: [[1.0, 0.0, 0.0] for _ in texts])
    monkeypatch.setattr(assistant, "embed_texts", lambda texts: [[1.0, 0.0, 0.0] for _ in texts])
    monkeypatch.setattr(assistant, "maybe_call_llm", lambda prompt, context: "The updated report identifies a critical schedule risk. [S1]")
    vector_store.reset_client()

    with TestClient(app) as client:
        first = client.post(
            "/documents/upload",
            files=[("files", ("weekly-status.txt", b"Project Alpha baseline report.", "text/plain"))],
        ).json()["results"][0]
        second = client.post(
            "/documents/upload",
            files=[("files", ("weekly-status.txt", b"Project Alpha has a critical schedule risk; action item: resolve the dependency.", "text/plain"))],
        ).json()["results"][0]

        assert first["status"] == "indexed"
        assert second["status"] == "updated"
        assert second["document_version"] == 2
        assert second["previous_document_id"] == first["document_id"]
        assert len(embedding_batches) == 2
        assert all("baseline report" not in text for text in embedding_batches[1])

        first_record = client.get(f"/documents/{first['document_id']}").json()
        second_record = client.get(f"/documents/{second['document_id']}").json()
        assert first_record["is_current_version"] == 0
        assert first_record["document_version"] == 1
        assert second_record["is_current_version"] == 1
        assert second_record["version_of_document_id"] == first["document_id"]

        results = client.post("/search", json={"query": "What is the current schedule risk?", "top_k": 5}).json()["results"]
        assert results
        assert {item["document_id"] for item in results} == {second["document_id"]}
        assert "critical schedule risk" in results[0]["text"]
        assistant_response = client.post("/assistant/chat", json={
            "session_id": "bd2ad170-e6a8-4bdb-b7a1-2db363e55c3b",
            "question": "What schedule risk was identified?",
            "top_k": 3,
        })
        assert assistant_response.status_code == 200
        assert {item["document_id"] for item in assistant_response.json()["sources"]} == {second["document_id"]}

        insights = client.get("/project-insights")
        assert insights.status_code == 200
        assert insights.json()["health"]["document_count"] == 1
        assert insights.json()["risks"]
        assert insights.json()["actions"]

        assert client.get(f"/documents/{first['document_id']}/text").status_code == 200
        assert client.delete(f"/documents/{second['document_id']}").status_code == 200
        restored = client.get(f"/documents/{first['document_id']}").json()
        assert restored["is_current_version"] == 1
    vector_store.reset_client()


def test_incremental_upload_reuses_hash_and_retrieves_existing_and_new_documents(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "platform-data"))
    embedding_batches = []

    def fake_embed(texts):
        embedding_batches.append(list(texts))
        return [[1.0, 0.0, 0.0] for _ in texts]

    monkeypatch.setattr(pipeline, "embed_texts", fake_embed)
    monkeypatch.setattr(search, "embed_texts", lambda texts: [[1.0, 0.0, 0.0] for _ in texts])
    monkeypatch.setattr(assistant, "embed_texts", lambda texts: [[1.0, 0.0, 0.0] for _ in texts])
    monkeypatch.setattr(assistant, "maybe_call_llm", lambda prompt, context: "The evidence is available. [S1]")
    vector_store.reset_client()

    with TestClient(app) as client:
        first = client.post("/documents/upload", files=[
            ("files", ("portfolio.txt", b"Project Atlas goal: deliver a baseline portfolio.", "text/plain")),
        ]).json()["results"][0]
        first_vector_ids = set(vector_store.get_collection().get(
            where={"document_id": first["document_id"]},
        )["ids"])
        second = client.post("/documents/upload", files=[
            ("files", ("integration.md", b"Kestrel integration requires a secure launch checklist.", "text/markdown")),
        ]).json()["results"][0]
        all_vector_ids = set(vector_store.get_collection().get()["ids"])

        assert first["status"] == second["status"] == "indexed"
        assert first_vector_ids and first_vector_ids.issubset(all_vector_ids)
        assert len(embedding_batches) == 2
        assert client.get("/documents").json()["total"] == 2

        duplicate = client.post("/documents/upload", files=[
            ("files", ("renamed-copy.md", b"Kestrel integration requires a secure launch checklist.", "text/markdown")),
        ]).json()["results"][0]
        assert duplicate["duplicate"] is True
        assert duplicate["document_id"] == second["document_id"]
        assert len(embedding_batches) == 2
        assert set(vector_store.get_collection().get()["ids"]) == all_vector_ids
        assert client.get("/documents").json()["total"] == 2

        search_results = client.post("/search", json={
            "query": "Project Atlas and Kestrel integration",
            "top_k": 50,
        }).json()["results"]
        assert {item["document_id"] for item in search_results} == {
            first["document_id"], second["document_id"],
        }

        assistant_result = client.post("/assistant/chat", json={
            "session_id": "bcab8c0b-fd67-4c97-9752-e8cf6455e95e",
            "question": "What does the Kestrel integration require?",
        }).json()
        assert assistant_result["answer_mode"] == "llm"
        assert assistant_result["sources"]
        assert {source["document_id"] for source in assistant_result["sources"]} == {second["document_id"]}

        assert client.delete(f"/documents/{second['document_id']}").status_code == 200
        after_delete = client.post("/search", json={
            "query": "Kestrel integration launch checklist",
            "top_k": 50,
        }).json()["results"]
        assert all(item["document_id"] != second["document_id"] for item in after_delete)
        assert set(vector_store.get_collection().get()["ids"]) == first_vector_ids

    vector_store.reset_client()


def test_failed_new_document_version_preserves_current_index_and_retrieval(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "platform-data"))

    def fail_new_version(texts):
        if any("FAIL NEW VERSION" in text for text in texts):
            raise RuntimeError("deliberate test embedding failure")
        return [[1.0, 0.0, 0.0] for _ in texts]

    monkeypatch.setattr(pipeline, "embed_texts", fail_new_version)
    monkeypatch.setattr(search, "embed_texts", lambda texts: [[1.0, 0.0, 0.0] for _ in texts])
    vector_store.reset_client()

    with TestClient(app) as client:
        original = client.post("/documents/upload", files=[
            ("files", ("release-plan.txt", b"Project Atlas release plan is current.", "text/plain")),
        ]).json()["results"][0]
        original_vector_ids = set(vector_store.get_collection().get(
            where={"document_id": original["document_id"]},
        )["ids"])
        failed = client.post("/documents/upload", files=[
            ("files", ("release-plan.txt", b"FAIL NEW VERSION: replacement release plan.", "text/plain")),
        ]).json()["results"][0]

        assert failed["status"] == "failed"
        assert client.get(f"/documents/{original['document_id']}").json()["is_current_version"] == 1
        failed_document = client.get(f"/documents/{failed['document_id']}").json()
        assert failed_document["processing_status"] == "failed"
        assert failed_document["is_current_version"] == 0
        assert set(vector_store.get_collection().get()["ids"]) == original_vector_ids

        results = client.post("/search", json={
            "query": "current release plan",
            "top_k": 10,
        }).json()["results"]
        assert results
        assert {item["document_id"] for item in results} == {original["document_id"]}

    vector_store.reset_client()
