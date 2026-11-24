from fastapi.testclient import TestClient
import pytest

from app.main import app
from app.api.routes import search
from app.schemas.analysis import EvidenceReference, RiskAnalysis, RiskFinding, ScopeAnalysis, classify_risk_severity
from app.services import pipeline, project_agents, vector_store


@pytest.mark.parametrize(("impact", "likelihood", "expected"), [
    ("High", "Medium", "High"),
    ("Critical", "Low", "Medium"),
    ("Medium", "Medium", "Medium"),
    ("Low", "Low", "Low"),
    ("High", "High", "High"),
    ("Critical", "High", "Critical"),
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


def test_risk_severity_infers_only_evidence_supported_dependency_dimensions() -> None:
    risk = RiskFinding(
        id="pending-dependency",
        title="Attendance import dependency",
        description="Impact: Blocks reliable import validation; Status: Pending; Due 2026-11-05.",
        impact="Blocks reliable import validation",
        evidence=[],
    )

    assert risk.level == "High"
    assert risk.impact == "High"
    assert risk.likelihood == "High"
    assert risk.probability_score == 3
    assert risk.impact_score == 3
    assert risk.severity_score == 9
    assert "pending" in risk.severity_reason.lower()
    assert risk.insufficient_evidence == []


def test_risk_severity_keeps_insufficient_dimensions_unrated() -> None:
    risk = RiskFinding(
        id="unknown-dependency",
        title="Unspecified dependency",
        description="A dependency is listed without status, impact, or probability.",
        evidence=[],
    )

    assert risk.level is None
    assert risk.insufficient_evidence == ["impact", "probability"]
    assert "impact and probability" in (risk.severity_reason or "")


def test_risk_severity_normalizes_case_and_scores_from_dimensions() -> None:
    risk = RiskFinding(
        id="case-normalization",
        title="Normalized risk",
        level="HIGH",
        impact="HIGH",
        likelihood="medium",
        description="A documented delivery risk.",
        evidence=[],
    )

    assert risk.level == "High"
    assert risk.impact == "High"
    assert risk.likelihood == "Medium"
    assert risk.severity_score == 6
    assert risk.severity_reason.startswith("Probability Medium (2) x impact High (3)")


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


def test_scope_analysis_keeps_one_goal_and_separates_objectives_with_evidence(monkeypatch) -> None:
    document = {
        "id": "campus-charter",
        "filename": "campus-charter.txt",
        "updated_at": "v1",
        "indexed_chunk_count": 1,
        "extracted_text": (
            "Main project goal: Create a campus portal that brings attendance and student support together.\n"
            "Problem statement: Manual attendance and separate support processes make progress difficult to track.\n"
            "Expected outcome: One portal gives students and faculty a shared attendance and support workflow.\n"
            "Supporting Objective 1: Students sign in securely to access the portal.\n"
            "Supporting Objective 2: Lecturers record attendance for each class session.\n"
            "Deliverable: Attendance dashboard\nDue date: 2026-12-01\nOwner: Faculty coordinator\nStatus: Planned"
        ),
    }
    chunk = {
        "id": "campus-charter_000000",
        "text": document["extracted_text"],
        "location": {},
    }
    monkeypatch.setattr(project_agents, "_load_documents", lambda **_kwargs: [document])
    monkeypatch.setattr(project_agents, "_load_chunks_for_document", lambda _document_id: [chunk])

    result = project_agents.analyze_scope(
        document_ids=[document["id"]],
        persist=False,
        use_llm=False,
    )

    assert result["main_project_goal"]["statement"] == (
        "Create a campus portal that brings attendance and student support together."
    )
    assert result["main_project_goal"]["problem"]
    assert result["main_project_goal"]["expected_outcome"]
    assert len(result["supporting_objectives"]) == 2
    assert all(item["evidence"][0]["chunk_id"] == chunk["id"] for item in result["supporting_objectives"])
    assert not any("Supporting Objective" in goal for goal in result["goals"])
    assert result["milestones"][0]["owner"] == "Faculty coordinator"


def test_scope_without_explicit_goal_requires_clarification(monkeypatch) -> None:
    document = {
        "id": "objectives-only",
        "filename": "objectives.txt",
        "updated_at": "v1",
        "indexed_chunk_count": 1,
        "extracted_text": "Objective: Lecturers record attendance for each class session.",
    }
    chunk = {"id": "objectives-only_000000", "text": document["extracted_text"], "location": {}}
    monkeypatch.setattr(project_agents, "_load_documents", lambda **_kwargs: [document])
    monkeypatch.setattr(project_agents, "_load_chunks_for_document", lambda _document_id: [chunk])

    result = project_agents.analyze_scope(
        document_ids=[document["id"]],
        persist=False,
        use_llm=False,
    )

    assert result["main_project_goal"] is None
    assert "needs clarification" in " ".join(result["missing_information"]).casefold()


def test_structured_agent_cache_uses_document_versions(monkeypatch) -> None:
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.setattr(project_agents, "_agent_context", lambda _documents, _agent: "verified evidence")
    calls = []

    def fake_llm(_prompt, _context, *, structured=False):
        assert structured is True
        calls.append(True)
        return '{"items":[]}'

    monkeypatch.setattr(project_agents, "maybe_call_llm", fake_llm)
    document = {
        "id": "versioned-cache-test",
        "sha256": "digest-v1",
        "updated_at": "2026-11-24T10:00:00Z",
        "document_version": 1,
        "indexed_chunk_count": 1,
    }
    first = project_agents._run_structured_agent("Cache test agent", [document], '{"items":[]}')
    second = project_agents._run_structured_agent("Cache test agent", [document], '{"items":[]}')
    updated = {**document, "sha256": "digest-v2", "document_version": 2}
    third = project_agents._run_structured_agent("Cache test agent", [updated], '{"items":[]}')

    assert first == second == third == {"items": []}
    assert len(calls) == 2


def test_concurrent_structured_agent_requests_share_one_generation(monkeypatch) -> None:
    import threading
    from concurrent.futures import Future, ThreadPoolExecutor

    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.setattr(project_agents, "_agent_context", lambda _documents, _agent: "verified evidence")
    generation_started = threading.Event()
    waiting_request_joined = threading.Event()
    release_generation = threading.Event()
    calls = []

    class TrackingFuture(Future):
        def result(self, *args, **kwargs):
            waiting_request_joined.set()
            return super().result(*args, **kwargs)

    def fake_llm(_prompt, _context, *, structured=False):
        assert structured is True
        calls.append(True)
        generation_started.set()
        assert release_generation.wait(timeout=5)
        return '{"items":[]}'

    monkeypatch.setattr(project_agents, "Future", TrackingFuture)
    monkeypatch.setattr(project_agents, "maybe_call_llm", fake_llm)
    document = {
        "id": "concurrent-cache-test",
        "sha256": "digest-v1",
        "updated_at": "2026-11-24T10:00:00Z",
        "document_version": 1,
        "indexed_chunk_count": 1,
    }

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(
            project_agents._run_structured_agent, "Concurrent cache test agent", [document], '{"items":[]}',
        )
        assert generation_started.wait(timeout=2)
        second = executor.submit(
            project_agents._run_structured_agent, "Concurrent cache test agent", [document], '{"items":[]}',
        )
        assert waiting_request_joined.wait(timeout=2)
        release_generation.set()
        assert first.result(timeout=2) == second.result(timeout=2) == {"items": []}

    assert len(calls) == 1


def test_scope_llm_extracts_one_goal_and_multiple_supported_objectives(monkeypatch) -> None:
    source = "campus-charter.txt"
    text = (
        "Create one campus portal that combines attendance and student support services. "
        "Manual attendance and disconnected support services are difficult to coordinate. "
        "A shared portal will make attendance and support status easier to access. "
        "Lecturers can record attendance for each class. Students can submit a support request."
    )
    document = {"id": "campus-llm", "filename": source, "extracted_text": text}

    def evidence_for(_documents, requested_source, quote):
        if requested_source == source and quote in text:
            return EvidenceReference(document_id=document["id"], filename=source, excerpt=quote)
        return None

    monkeypatch.setattr(project_agents, "_agent_evidence", evidence_for)
    monkeypatch.setattr(project_agents, "_run_structured_agent", lambda *_args, **_kwargs: {
        "main_project_goal": {
            "statement": "Create one campus portal that combines attendance and student support services.",
            "source": source,
            "quote": "Create one campus portal that combines attendance and student support services.",
            "problem": "Manual attendance and disconnected support services are difficult to coordinate.",
            "problem_source": source,
            "problem_quote": "Manual attendance and disconnected support services are difficult to coordinate.",
            "expected_outcome": "A shared portal will make attendance and support status easier to access.",
            "outcome_source": source,
            "outcome_quote": "A shared portal will make attendance and support status easier to access.",
        },
        "goals": [],
        "objectives": [
            {"description": "Lecturers can record attendance for each class.", "source": source,
             "quote": "Lecturers can record attendance for each class."},
            {"description": "Students can submit a support request.", "source": source,
             "quote": "Students can submit a support request."},
        ],
        "scope": [],
        "deliverables": [],
        "milestones": [],
        "responsibilities": [],
        "dependencies": [],
    })

    result = project_agents._llm_scope_analysis([document])

    assert result["main_project_goal"]["statement"].startswith("Create one campus portal")
    assert len(result["supporting_objectives"]) == 2
    assert result["main_project_goal"]["problem_evidence"]
    assert result["main_project_goal"]["outcome_evidence"]


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


def test_incremental_upload_persists_metadata_and_detects_duplicates(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "incremental-data"))
    monkeypatch.setattr(pipeline, "embed_texts", lambda texts: [[1.0, 0.0, 0.0] for _ in texts])
    monkeypatch.setattr(search, "embed_texts", lambda texts: [[1.0, 0.0, 0.0] for _ in texts])
    vector_store.reset_client()

    with TestClient(app) as client:
        first = client.post(
            "/documents/incremental/upload",
            data={
                "category": "Meeting Notes",
                "title": "Weekly meeting",
                "description": "Latest project update",
                "project_name": "Analytics Platform",
                "milestone_name": "Pilot release",
            },
            files=[("files", ("meeting.txt", b"Milestone 2 is delayed by vendor approval.", "text/plain"))],
        )
        assert first.status_code == 200
        first_result = first.json()["results"][0]
        assert first_result["status"] == "indexed"

        duplicate = client.post(
            "/documents/incremental/upload",
            data={"category": "Meeting Notes"},
            files=[("files", ("meeting.txt", b"Milestone 2 is delayed by vendor approval.", "text/plain"))],
        )
        assert duplicate.status_code == 200
        duplicate_result = duplicate.json()["results"][0]
        assert duplicate_result["duplicate"] is True

        document = client.get(f"/documents/{first_result['document_id']}").json()
        assert document["document_category"] == "Meeting Notes"
        assert document["document_title"] == "Weekly meeting"
        assert document["project_name"] == "Analytics Platform"
        assert document["milestone_name"] == "Pilot release"
        assert document["change_type"] == "unchanged"

    vector_store.reset_client()


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
