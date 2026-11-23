from fastapi.testclient import TestClient
import pytest

from app.main import app
from app.api.routes import search
from app.schemas.analysis import RiskAnalysis, RiskFinding, ScopeAnalysis, classify_risk_severity
from app.services import pipeline, project_agents, vector_store


@pytest.mark.parametrize(("impact", "likelihood", "expected"), [
    ("High", "Medium", "High"),
    ("Critical", "Low", "High"),
    ("Medium", "Medium", "Medium"),
    ("Low", "Low", "Low"),
    ("High", None, None),
    (None, "High", None),
])
def test_risk_severity_uses_only_documented_impact_and_likelihood(impact, likelihood, expected) -> None:
    assert classify_risk_severity(impact, likelihood) == expected


def test_risk_summary_counts_match_findings_and_keep_unrated_separate() -> None:
    analysis = RiskAnalysis(findings=[
        RiskFinding(id="r1", title="One", description="Risk one", impact="High", likelihood="Medium", evidence=[]),
        RiskFinding(id="r2", title="Two", description="Risk two", level="High", evidence=[]),
        RiskFinding(id="r3", title="Three", description="Risk three", impact="Low", likelihood="Low", evidence=[]),
        RiskFinding(id="r4", title="Four", description="Risk four", evidence=[]),
    ])
    assert analysis.severity_counts == {"Critical": 0, "High": 2, "Medium": 0, "Low": 1, "Unrated": 1}


def test_scope_merge_does_not_mislabel_deliverables_as_goals_or_objectives() -> None:
    source = ScopeAnalysis(
        deliverables=["Requirements and workflow document; Due 2026-11-06"],
    ).model_dump(mode="json")
    model = ScopeAnalysis(
        goals=["Requirements and workflow document"],
        objectives=["Clickable interface prototype"],
        deliverables=["Clickable interface prototype; Due 2026-11-13"],
    ).model_dump(mode="json")
    merged = project_agents._merge_scope_results(source, model)
    assert "Requirements and workflow document" not in merged["goals"]
    assert "Clickable interface prototype" not in merged["objectives"]
    assert any(item.startswith("Clickable interface prototype") for item in merged["deliverables"])


def test_unusable_structured_response_is_reported_for_source_fallback() -> None:
    def incomplete(_documents):
        raise project_agents.LLMGenerationError("model returned incomplete JSON")

    result, warning = project_agents._try_structured_agent(incomplete, [])

    assert result is None
    assert warning == "model returned incomplete JSON"


def test_agent_configuration_errors_remain_visible() -> None:
    def misconfigured(_documents):
        raise project_agents.LLMConfigurationError("model is not configured")

    with pytest.raises(project_agents.LLMConfigurationError, match="not configured"):
        project_agents._try_structured_agent(misconfigured, [])


def test_agent_retrieval_context_is_cached_by_document_version(monkeypatch) -> None:
    from app.services import assistant

    retrieval_calls = []
    document = {
        "id": "agent-context-cache-test",
        "filename": "project-plan.txt",
        "updated_at": "version-1",
        "indexed_chunk_count": 1,
    }

    def retrieve(query, top_k, document_ids, timings):
        retrieval_calls.append((query, tuple(document_ids)))
        return [{
            "id": "agent-context-cache-test_000000",
            "text": f"Project goal version {len(retrieval_calls)} is to deliver the attendance portal.",
            "metadata": {"document_id": document["id"], "filename": document["filename"], "chunk_index": 0},
        }]

    monkeypatch.setattr(assistant, "retrieve_relevant_chunks", retrieve)
    monkeypatch.setattr(assistant, "_relevant_sentences", lambda text, query, limit=4: [(1.0, text)])

    first = project_agents._agent_context([document], "Scope and Deliverable Extraction Agent")
    repeated = project_agents._agent_context([document], "Scope and Deliverable Extraction Agent")
    assert repeated == first
    assert len(retrieval_calls) == 1

    updated = {**document, "updated_at": "version-2"}
    refreshed = project_agents._agent_context([updated], "Scope and Deliverable Extraction Agent")
    assert refreshed != first
    assert len(retrieval_calls) == 2


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
        scope_descriptions = [item["description"].casefold() for item in scope["findings"]]
        assert len(scope_descriptions) == len(set(scope_descriptions))
        assert all(not item.startswith("risk description:") for item in scope_descriptions)
        assert any("analytics platform" in item.lower() for item in scope["goals"])
        assert all("project-plan.txt" not in item for item in scope["goals"])

        risks = client.post("/analysis/risks", json={"document_ids": document_ids}).json()
        assert risks["findings"]
        assert sum(risks["severity_counts"].values()) == len(risks["findings"])
        for finding in risks["findings"]:
            if finding.get("impact") and finding.get("likelihood"):
                assert finding["level"] is not None
        assert any("delayed" in item["description"].lower() or "blocked" in item["description"].lower() for item in risks["findings"])
        assert all(item["level"] is None or item["level"] in {"Low", "Medium", "High", "Critical"} for item in risks["findings"])
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
