from fastapi.testclient import TestClient

from app.main import app
from app.api.routes import search
from app.services import pipeline, vector_store


def test_project_analysis_agents_extract_scope_risk_and_blockers(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "platform-data"))
    monkeypatch.setenv("CHUNK_SIZE_TOKENS", "18")
    monkeypatch.setenv("CHUNK_OVERLAP_TOKENS", "2")
    monkeypatch.setattr(pipeline, "embed_texts", lambda texts: [[1.0, 0.0, 0.0] for _ in texts])
    monkeypatch.setattr(search, "embed_texts", lambda texts: [[1.0, 0.0, 0.0] for _ in texts])
    vector_store.reset_client()

    with TestClient(app) as client:
        response = client.post(
            "/documents/upload",
            files=[
                (
                    "files",
                    (
                        "project-plan.txt",
                        b"Project goal is to deliver the analytics platform by Q3 2026.\n"
                        b"The primary deliverable is a unified dashboard.\n"
                        b"Milestone 1 is due 2026-07-01 and the launch page owner is Alice.\n"
                        b"The reporting team depends on security sign-off before rollout.\n"
                        b"Status: On track with moderate progress.\n",
                        "text/plain",
                    ),
                ),
                (
                    "files",
                    (
                        "meeting-notes.txt",
                        b"Meeting notes: the database migration is blocked by a missing vendor approval.\n"
                        b"1. Blocker: API integration is delayed because authentication approval is pending.\n"
                        b"2. Action item: Ravi must complete API integration by Friday.\n"
                        b"3. Pending decision: The team must decide which database to use.\n"
                        b"The sponsor needs a decision on the rollout window.\n"
                        b"Action item: Priya will validate the data model by 2026-08-15.\n"
                        b"The project is delayed because infrastructure testing is behind schedule.\n"
                        b"Vendor approval is pending and the release is delayed because credentials are missing.\n"
                        b"Ravi must complete the access review by Friday.\n"
                        b"The team must decide whether to use the current deployment approach.\n",
                        "text/plain",
                    ),
                ),
            ],
        )
        assert response.status_code == 200
        document_ids = [item["document_id"] for item in response.json()["results"] if item["status"] == "indexed"]
        assert len(document_ids) == 2

        scope = client.post("/analysis/scope", json={"document_ids": document_ids}).json()
        assert scope["goals"]
        assert any("deliver" in item.lower() for item in scope["deliverables"]) or scope["deliverables"]
        assert scope["dependencies"] or scope["missing_information"]
        assert scope["source_documents"]
        assert scope["findings"]

        risks = client.post("/analysis/risks", json={"document_ids": document_ids}).json()
        assert risks["findings"]
        assert any(item["level"] in {"Medium", "High", "Critical"} for item in risks["findings"])
        assert risks["source_documents"]

        blockers = client.post("/analysis/blockers", json={"document_ids": document_ids}).json()
        assert blockers["blockers"] or blockers["pending_decisions"] or blockers["action_items"]
        assert blockers["source_documents"]
        assert any(item["description"] for item in blockers["action_items"])
        assert any("API integration is delayed" in item["description"] for item in blockers["blockers"])
        assert any("must decide which database" in item["description"] for item in blockers["pending_decisions"])
        ravi_action = next(item for item in blockers["action_items"] if "Ravi must complete" in item["description"])
        assert ravi_action["assignee"] == "Ravi"
        assert ravi_action["due_date"] == "Friday"
        assert ravi_action["priority"] is None
        assert any("credentials are missing" in item["description"] for item in blockers["blockers"])
        assert any("must decide whether" in item["description"] for item in blockers["pending_decisions"])
        unlabelled_action = next(item for item in blockers["action_items"] if "Ravi must complete the access review" in item["description"])
        assert unlabelled_action["assignee"] == "Ravi"
        assert unlabelled_action["due_date"] == "Friday"

        for findings in (
            scope["findings"],
            risks["findings"],
            blockers["blockers"] + blockers["pending_decisions"] + blockers["action_items"],
        ):
            matched_chunk_count = 0
            for finding in findings:
                assert finding["evidence"]
                evidence = finding["evidence"][0]
                assert evidence["document_id"] in document_ids
                assert evidence["filename"]
                if evidence["chunk_id"]:
                    matched_chunk_count += 1
                    assert evidence["excerpt"]
                    chunk_response = client.get(f"/chunks/{evidence['chunk_id']}")
                    assert chunk_response.status_code == 200
                    chunk = chunk_response.json()
                    assert chunk["document_id"] == evidence["document_id"]
                    assert evidence["excerpt"] in chunk["text"]
            assert matched_chunk_count > 0

    vector_store.reset_client()
