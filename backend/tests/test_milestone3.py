import json
import sys
from datetime import date, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.api.routes import project_insights, search
from app.core.config import settings
from app.database.db import connect
from app.services import assistant, pipeline, project_agents, vector_store
from app.services.project_agents import _structured_deliverable_milestones
from app.services.project_agents import (
    LLMConfigurationError,
    LLMGenerationError,
    LLMTimeoutError,
    maybe_call_llm,
    maybe_call_llm as configured_llm,
)
from app.services.project_health import _blocker_dimension, _classify_score, _timeline_dimension


def test_project_insights_does_not_count_explicitly_absent_blockers() -> None:
    assert not project_insights._is_unresolved_blocker({
        "description": "There are no unresolved blockers.",
    })
    assert not project_insights._is_unresolved_blocker({
        "description": "No open blockers remain.",
    })
    assert project_insights._is_unresolved_blocker({
        "description": "The release is waiting for security approval.",
    })


def test_project_health_does_not_penalize_explicit_no_unresolved_blockers() -> None:
    dimension, unresolved, decisions = _blocker_dimension(
        {
            "blockers": [{
                "status": "open",
                "description": "There are no unresolved blockers.",
            }],
            "pending_decisions": [],
            "action_items": [],
        },
        "There are no unresolved blockers.",
    )

    assert dimension["score"] == 100
    assert unresolved == []
    assert decisions == []


def test_health_classification_boundaries() -> None:
    assert _classify_score(100) == "Healthy"
    assert _classify_score(80) == "Healthy"
    assert _classify_score(79.9) == "Needs Attention"
    assert _classify_score(60) == "Needs Attention"
    assert _classify_score(59.9) == "At Risk"
    assert _classify_score(0) == "At Risk"


def test_timeline_requires_incomplete_status_before_inferring_overdue() -> None:
    risks = {"findings": []}
    identified_only = {
        "milestones": [{
            "name": "Due date: 2026-11-06",
            "date": "2026-11-06",
            "status": "identified",
            "description": "Due date: 2026-11-06",
        }],
    }
    _, delayed = _timeline_dimension(identified_only, risks, today=date(2026, 11, 10))
    assert delayed == []

    in_progress = {
        "milestones": [{
            "name": "Requirements document",
            "date": "2026-11-06",
            "status": "In progress",
            "description": "Requirements document is in progress.",
        }],
    }
    _, delayed = _timeline_dimension(in_progress, risks, today=date(2026, 11, 10))
    assert len(delayed) == 1


def test_scope_extracts_deliverable_due_date_and_status_from_workbook_row() -> None:
    text = (
        "Workbook: register.xlsx\nWorksheet: Deliverables\nRow: 2\n"
        "Deliverable: Requirements document\nOwner: Project lead\n"
        "Due date: 2026-11-06\nStatus: In progress"
    )
    document = {"id": "document-1", "filename": "register.xlsx", "extracted_text": text}
    milestones = _structured_deliverable_milestones(
        document,
        [{"id": "chunk-1", "text": text, "location": {}}],
    )
    assert len(milestones) == 1
    assert milestones[0].name == "Requirements document"
    assert milestones[0].date == "2026-11-06"
    assert milestones[0].status == "In progress"
    assert milestones[0].evidence[0].chunk_id == "chunk-1"


def test_assistant_retrieval_rejects_generic_overlap_and_filters_testing_guide(monkeypatch) -> None:
    assert assistant._relevant_sentences(
        "The project team meets weekly.",
        "What is the project team's favorite dessert?",
    ) == []
    deadline_terms = assistant._retrieval_terms("What is the current project deadline?")
    assert "due date" in deadline_terms
    assert "milestone" in deadline_terms
    assert "in progress" in assistant._retrieval_terms("Are we on track?")
    dependency_terms = assistant._retrieval_terms("What are the major project dependencies?")
    assert "dependency" in dependency_terms
    assert "depends on" in dependency_terms
    focus_terms = assistant._retrieval_terms("What should the project team focus on next?")
    assert "action item" in focus_terms
    assert assistant._relevant_sentences(
        "Dependency: Clean attendance sample needed for import validation.",
        "What are the major project dependencies?",
    )

    guide_match = {
        "id": "guide-chunk",
        "text": "Milestones are delayed.",
        "distance": 0.1,
        "metadata": {
            "document_id": "guide-document",
            "filename": "08_README_Testing_Guide.txt",
        },
    }
    monkeypatch.setattr(assistant, "embed_texts", lambda texts: [[1.0, 0.0, 0.0] for _ in texts])
    monkeypatch.setattr(assistant, "query_vectors", lambda *args, **kwargs: [guide_match])
    monkeypatch.setattr(assistant, "_lexical_candidates", lambda question, document_ids: [])
    assert assistant.retrieve_relevant_chunks("Which milestones are delayed?") == []
    assert assistant.retrieve_relevant_chunks(
        "Which milestones are delayed?",
        document_ids=["guide-document"],
    )


def test_assistant_answers_structured_register_questions_from_matching_rows() -> None:
    def source(filename: str, sheet: str, excerpt: str) -> dict:
        return {
            "document_id": "doc-1",
            "filename": filename,
            "chunk_id": f"{sheet}-chunk",
            "excerpt": excerpt,
            "location": {"sheet_name": sheet, "row_number": 2},
            "distance": 0.1,
        }

    sources = [
        source("04_Project_Registers.xlsx", "Deliverables",
            "Workbook: 04_Project_Registers.xlsx Worksheet: Deliverables Row: 2 "
            "Deliverable: Requirements and workflow document Owner: Project lead "
            "Due date: 2026-11-06 Status: In progress Workbook: 04_Project_Registers.xlsx "
            "Worksheet: Deliverables Row: 7 Deliverable: Pilot test report and handover guide "
            "Owner: QA coordinator Due date: 2026-12-11 Status: Not started"),
        source("04_Project_Registers.xlsx", "Action Items",
            "Workbook: 04_Project_Registers.xlsx Worksheet: Action Items Row: 2 "
            "Action: Confirm authentication provider access Owner: Ravi / Backend developer "
            "Due date: 2026-11-04 Status: Open Workbook: 04_Project_Registers.xlsx "
            "Worksheet: Action Items Row: 3 Action: Share a cleaned attendance spreadsheet "
            "Owner: Meena / Data coordinator Due date: 2026-11-05 Status: In progress "
            "Workbook: 04_Project_Registers.xlsx Worksheet: Action Items Row: 5 "
            "Action: Choose notification approach for pilot Owner: Project lead and IT support "
            "Due date: 2026-11-06 Status: Pending decision Notes: Email or in-app-only status"),
        source("04_Project_Registers.xlsx", "Dependencies",
            "Workbook: 04_Project_Registers.xlsx Worksheet: Dependencies Row: 2 "
            "Dependency: Authentication integration Required input: Identity-provider access approval "
            "Owner: Backend developer Due date: 2026-11-04 Status: Not confirmed "
            "Workbook: 04_Project_Registers.xlsx Worksheet: Dependencies Row: 3 "
            "Dependency: Attendance import Required input: Clean sample spreadsheet and agreed ID format "
            "Owner: Data coordinator Due date: 2026-11-05 Status: Pending"),
        source("04_Project_Registers.xlsx", "Risk Register",
            "Workbook: 04_Project_Registers.xlsx Worksheet: Risk Register Row: 2 "
            "Risk description: Authentication provider access may be delayed Impact: High "
            "Likelihood: Medium Owner: Backend developer Mitigation: Confirm access by 2026-11-04 "
            "Status: Open Workbook: 04_Project_Registers.xlsx Worksheet: Risk Register Row: 3 "
            "Risk description: Student IDs may be inconsistent across spreadsheets Impact: High "
            "Likelihood: High Owner: Data coordinator Status: Open"),
    ]

    deadline = assistant._structured_project_answer("What is the current project deadline?", sources)
    assert deadline is not None
    assert "Pilot test report and handover guide" in deadline[0]
    assert "2026-12-11" in deadline[0]
    assert deadline[1][0]["location"] == {"sheet_name": "Deliverables", "row_number": 7}

    tasks = assistant._structured_project_answer("Who owns the pending tasks?", sources)
    assert tasks is not None
    assert "Ravi / Backend developer" in tasks[0]
    assert "Meena / Data coordinator" in tasks[0]
    assert "Project lead and IT support" in tasks[0]
    assert "department coo" not in tasks[0].casefold()

    dependencies = assistant._structured_project_answer("What are the major dependencies?", sources)
    assert dependencies is not None
    assert "Identity-provider access approval" in dependencies[0]
    assert "Backend developer" in dependencies[0]
    assert "Data coordinator" in dependencies[0]

    decisions = assistant._structured_project_answer("What decisions remain unresolved?", sources)
    assert decisions is not None
    assert "Email or in-app-only status" in decisions[0]
    assert "Project lead and IT support" in decisions[0]

    risks = assistant._structured_project_answer("What are the biggest project risks?", sources)
    assert risks is not None
    assert "impact: High" in risks[0]
    assert "owner: Backend developer" in risks[0]
    assert risks[0].index("Student IDs") < risks[0].index("Authentication provider")
    assert "department coo" not in risks[0].casefold()


def test_assistant_returns_structured_answers_without_calling_the_llm(tmp_path, monkeypatch) -> None:
    _configure_isolated_platform(tmp_path, monkeypatch)
    source = {
        "document_id": "doc-1",
        "filename": "04_Project_Registers.xlsx",
        "chunk_id": "deliverables-1",
        "excerpt": (
            "Workbook: 04_Project_Registers.xlsx Worksheet: Deliverables Row: 7 "
            "Deliverable: Pilot test report and handover guide Owner: QA coordinator "
            "Due date: 2026-12-11 Status: Not started"
        ),
        "location": {"sheet_name": "Deliverables", "row_number": 2},
        "distance": 0.1,
    }
    monkeypatch.setattr(assistant, "retrieve_relevant_chunks", lambda *args, **kwargs: [])
    monkeypatch.setattr(assistant, "_source_records", lambda *args, **kwargs: [source])
    monkeypatch.setattr(
        assistant,
        "maybe_call_llm",
        lambda *args, **kwargs: pytest.fail("Structured register questions must not call the LLM."),
    )

    with TestClient(app) as client:
        response = client.post("/assistant/chat", json={
            "session_id": str(uuid4()),
            "question": "What is the current project deadline?",
        })

    assert response.status_code == 200
    result = response.json()
    assert result["answer_mode"] == "extractive"
    assert "2026-12-11" in result["answer"]
    assert result["sources"][0]["location"] == {"sheet_name": "Deliverables", "row_number": 7}
    vector_store.reset_client()


def test_assistant_compacts_health_context_and_deduplicates_source_evidence() -> None:
    old_report_date = date.today() - timedelta(days=8)
    health = {
        "id": "internal-health-id",
        "calculated_at": "2026-11-10T09:00:00+00:00",
        "overall_score": 51.8,
        "classification": "At Risk",
        "delayed_milestones": [{
            "name": "Requirements document",
            "date": "2026-11-06",
            "status": "In progress",
            "source": "register.xlsx",
        }],
        "unresolved_blockers": [{
            "id": "blocker-42",
            "description": "Authentication access is awaiting approval.",
            "source_document": "tracker.csv",
        }],
        "pending_decisions": [],
        "identified_risks": [{
            "id": "risk-99",
            "title": "Approval dependency",
            "level": "High",
            "description": "Authentication access is awaiting approval.",
            "evidence": [{"filename": "tracker.csv"}],
        }],
        "recommended_actions": ["Repeated recommendation"],
    }
    compact = assistant._health_context(health, old_report_date)
    assert "At Risk" in compact
    assert "51.8" in compact
    assert "historical" in compact
    assert "internal-health-id" not in compact
    assert "blocker-42" not in compact
    assert "risk-99" not in compact
    assert "Repeated recommendation" not in compact
    assert "null" not in compact.casefold()
    health_sources = [{
        "filename": "register.xlsx",
        "excerpt": (
            "Report Date: 2026-11-02. Requirements document is overdue. Department coordinator review is pending. "
            "Authentication provider access awaits IT approval; integrated sign-in testing cannot begin."
        ),
    }]
    summary = assistant._health_evidence_summary(health, health_sources, old_report_date)
    assert "**Overdue**" in summary
    assert "register.xlsx" in summary
    assert "historical, not confirmed current" not in summary
    assert "Request an updated status report" in summary
    assert "Recommendations (assistant-generated)" in summary
    assert "department coordinator's review" in summary
    assert "integrated sign-in testing cannot begin" in summary

    sources = [
        {"document_id": "doc-1", "chunk_id": "chunk-1", "excerpt": "Authentication access is awaiting approval."},
        {"document_id": "doc-1", "chunk_id": "chunk-2", "excerpt": "Authentication access is awaiting approval."},
        {"document_id": "doc-2", "chunk_id": "chunk-3", "excerpt": "Authentication access is awaiting approval."},
    ]
    deduplicated = assistant._deduplicate_sources(sources)
    assert [source["chunk_id"] for source in deduplicated] == ["chunk-1", "chunk-3"]


def test_assistant_extracts_latest_dated_status_report() -> None:
    sources = [
        {"excerpt": "Report Date: 2026-11-02 Workstream: Planning"},
        {"excerpt": "As of 2026-11-04, status review"},
        {"excerpt": "No report date is given."},
    ]
    assert assistant._latest_status_date(sources) == date(2026, 11, 4)


def test_assistant_strips_svg_and_unescapes_markdown_from_model_output() -> None:
    answer = (
        "**Answer**\nThe task is **blocked** \\[S1\\]. "
        "Risk R-04: Attendance is pending (Blocks reliable import validation, "
        "Impact: Blocks reliable import validation)."
        "<svg viewBox='0 0 1 1'><path d='M0'/></svg>"
    )
    cleaned = assistant._clean_generated_answer(answer)
    assert r"\*" not in cleaned
    assert r"\[" not in cleaned
    assert "<svg" not in cleaned
    assert "<path" not in cleaned
    assert "**Answer**" in cleaned
    assert "**blocked**" in cleaned
    assert "[S1]" not in cleaned
    assert " S1" not in cleaned
    assert "R-04" not in cleaned
    assert cleaned.count("Blocks reliable import validation") == 1


def test_assistant_converts_private_source_tokens_to_document_references() -> None:
    sources = [
        {
            "filename": "04_Project_Registers.xlsx",
            "location": {"sheet_name": "Dependencies", "row_number": 3},
        },
        {"filename": "02_Kickoff_Meeting_Minutes.pdf", "location": {"page_number": 1}},
    ]
    answer = "The import requires a clean sample [S1] [S1]. Approval blocks testing [S2]. Unknown [S6]."
    displayed = assistant._replace_source_labels(answer, sources)
    assert "S1" not in displayed
    assert "S2" not in displayed
    assert "S6" not in displayed
    assert displayed.count("04_Project_Registers.xlsx") == 1
    assert "Dependencies sheet, row 3" in displayed
    assert "02_Kickoff_Meeting_Minutes.pdf, page 1" in displayed


def test_generated_risk_answer_adds_direct_answer_sources_and_specific_recommendations() -> None:
    sources = [
        {
            "filename": "kickoff.pdf",
            "location": {"page_number": 1},
            "excerpt": "Authentication access awaits approval; integration testing cannot begin.",
        },
        {
            "filename": "register.xlsx",
            "location": {"sheet_name": "Risk Register", "row_number": 5},
            "excerpt": (
                "Email notifications may not be available. Mitigation: confirm email service. "
                "Action: Confirm authentication provider access; approval is needed before testing."
            ),
        },
        {
            "filename": "dependencies.xlsx",
            "location": {"sheet_name": "Dependencies", "row_number": 3},
            "excerpt": (
                "Attendance import requires a clean sample spreadsheet and agreed student ID format. "
                "Impact: Blocks reliable import validation."
            ),
        },
    ]
    answer = """**Current Project Risks**
Our biggest project risks are:
* **Authentication access**: awaiting approval (Blocker)
* **Email notifications**: may not be available in pilot
* **Attendance import**: pending (Blocks reliable import validation, Impact: Blocks reliable import validation)

**Recommendations (assistant-generated)**
1. Confirm the email service status and consider in-app status if approved for Risk R-04.
"""

    grounded = assistant._ground_generated_answer(
        answer,
        sources,
        "What are our biggest project risks?",
    )

    assert grounded.startswith("**Answer** The key documented risks are")
    assert "kickoff.pdf, page 1" in grounded
    assert "register.xlsx, Risk Register sheet, row 5" in grounded
    assert "dependencies.xlsx, Dependencies sheet, row 3" in grounded
    assert "Impact: Blocks reliable import validation, Impact:" not in grounded
    assert "Recommendations (assistant-generated)" in grounded
    authentication_finding = next(
        line for line in grounded.splitlines()
        if "awaiting approval" in line and "Authentication access" in line
    )
    assert "kickoff.pdf, page 1" in authentication_finding
    assert "register.xlsx" not in authentication_finding
    assert "integrated sign-in testing cannot begin" in grounded
    assert "Confirm the email service status" in grounded
    assert "register.xlsx, Risk Register sheet, row 5" in grounded
    assert "for Risk ." not in grounded
    assert "R-04" not in grounded
    assert "student-ID format" in grounded
    assert "S1" not in grounded


def test_health_recommendations_name_actual_project_dependencies() -> None:
    assert "integrated sign-in testing cannot begin" in assistant._specific_recommendation(
        "Authentication provider access has not been approved.",
        "Integrated sign-in testing cannot begin until access is approved.",
    )
    assert "cleaned attendance spreadsheet" in assistant._specific_recommendation(
        "Attendance import is pending.",
        "A clean sample spreadsheet and agreed ID format block import validation.",
    )
    assert "department coordinator's review" in assistant._specific_recommendation(
        "Requirements document is overdue.",
        "Department coordinator review is pending.",
    )
    assert "student-ID format" in assistant._specific_recommendation(
        "Student IDs may be inconsistent across spreadsheets.",
        "Risk description: Student IDs may be inconsistent across spreadsheets.",
    )
    assert "decision date" in assistant._specific_recommendation(
        "The team must decide whether in-app status is sufficient.",
        "Pending decision: in-app status or email notifications for the pilot.",
    )


def test_health_finding_uses_its_indexed_evidence_when_not_in_initial_top_k() -> None:
    sources: list[dict[str, object]] = []
    item = {
        "description": "Authentication provider access is awaiting approval.",
        "evidence": [{
            "document_id": "doc-auth",
            "filename": "status-tracker.csv",
            "chunk_id": "chunk-auth",
            "excerpt": "Row: 4 Authentication provider access is awaiting approval.",
            "location": {"row_number": 4},
        }],
    }

    matched = assistant._health_item_source(item, sources, "status-tracker.csv")

    assert matched is not None
    assert matched["filename"] == "status-tracker.csv"
    assert matched["location"] == {"row_number": 4}
    assert matched["distance"] is None
    assert [source["chunk_id"] for source in sources] == ["chunk-auth"]

    workbook_source = {
        "document_id": "doc-register",
        "filename": "register.xlsx",
        "chunk_id": "chunk-register",
        "excerpt": "Worksheet: Deliverables Row: 2 Deliverable: Student support module",
        "location": {"sheet_name": "Deliverables", "row_number": 2},
        "distance": 0.1,
    }
    workbook_sources = [workbook_source]
    risk_item = {
        "description": "Risk description: Student IDs may be inconsistent across spreadsheets",
        "evidence": [{
            "document_id": "doc-register",
            "filename": "register.xlsx",
            "chunk_id": "chunk-register",
            "excerpt": "Risk description: Student IDs may be inconsistent across spreadsheets",
            "location": {"sheet_name": "Deliverables", "row_number": 2},
        }],
    }
    selected = assistant._health_item_source(risk_item, workbook_sources, "register.xlsx")
    assert selected is workbook_source
    assert "Risk description: Student IDs may be inconsistent" in selected["excerpt"]
    assert selected["location"] == {}


def test_health_summary_deduplicates_project_topics_and_ignores_extractor_headings() -> None:
    health = {
        "delayed_milestones": [],
        "unresolved_blockers": [
            {
                "description": (
                    "Authentication provider access has not been approved; "
                    "integrated sign-in testing cannot begin."
                ),
                "source_document": "kickoff.pdf",
            },
            {
                "description": "Authentication access is awaiting approval.",
                "source_document": "stories.md",
            },
            {
                "description": "Risks, blocker, and decision.",
                "source_document": "tracker.csv",
            },
            {"description": "Status: Blocked", "source_document": "tracker.csv"},
        ],
        "pending_decisions": [
            {
                "description": (
                    "The project lead and IT support must decide whether in-app status "
                    "is sufficient for the pilot."
                ),
                "source_document": "kickoff.pdf",
            },
            {
                "description": (
                    "The team must choose email notifications or in-app-only status."
                ),
                "source_document": "stories.md",
            },
            {
                "description": "Owner: IT support; due: 2026-11-06; status: Pending decision",
                "source_document": "charter.docx",
            },
        ],
        "identified_risks": [
            {
                "description": "Risk description: Authentication access may be delayed.",
                "level": "High",
                "evidence": [{"filename": "kickoff.pdf"}],
            },
            {"description": "Status: At risk", "level": "High", "evidence": []},
        ],
    }
    sources = [
        {
            "filename": "kickoff.pdf",
            "chunk_id": "chunk-auth",
            "excerpt": (
                "Authentication provider access has not been approved; "
                "integrated sign-in testing cannot begin."
            ),
            "location": {},
        },
        {
            "filename": "charter.docx",
            "chunk_id": "chunk-decision",
            "excerpt": "The project lead and IT support must decide whether in-app status is sufficient.",
            "location": {},
        },
        {
            "filename": "risk-register.xlsx",
            "chunk_id": "chunk-risk",
            "excerpt": "Risk description: Authentication access may be delayed.",
            "location": {},
        },
    ]
    summary = assistant._health_evidence_summary(health, sources, None)

    assert summary.count("**Blocker**") == 1
    assert summary.count("**Pending decision**") == 1
    assert summary.count("**High risk**") == 1
    assert "Risks, blocker, and decision" not in summary
    assert "Status: Blocked" not in summary
    assert "Status: At risk" not in summary
    assert "Owner: IT support" not in summary
    assert "Risk description:" not in summary
    assert "Authentication access may be delayed" in summary
    assert "integrated sign-in testing cannot begin" in summary


def test_openai_answer_generation_uses_configured_model_and_grounding_context(monkeypatch, request) -> None:
    calls: dict[str, object] = {}

    class FakeResponses:
        def create(self, **kwargs):
            calls.update(kwargs)
            return SimpleNamespace(output_text="**Answer**\n- Supported [S1]")

    class FakeClient:
        def __init__(self, **kwargs):
            calls["client"] = kwargs
            self.responses = FakeResponses()

    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "unit-test-key")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeClient))
    monkeypatch.setenv("LLM_REQUEST_TIMEOUT_SECONDS", "17")
    request.addfinalizer(project_agents._openai_client.cache_clear)
    answer = maybe_call_llm(
        "Use evidence only.",
        "[S1] Source: plan.txt (chunk id)\nEvidence: Deadline is Friday.",
    )
    assert answer == "**Answer**\n- Supported [S1]"
    assert calls["model"] == "test-model"
    assert calls["instructions"] == "Use evidence only."
    assert "Deadline is Friday" in calls["input"]
    assert calls["client"]["api_key"] == "unit-test-key"
    assert calls["client"]["timeout"] == 17
    assert calls["max_output_tokens"] == 384


def test_openai_answer_generation_reports_service_failure(monkeypatch) -> None:
    class FakeResponses:
        def create(self, **kwargs):
            raise RuntimeError("provider unavailable")

    class FakeClient:
        def __init__(self, **kwargs):
            self.responses = FakeResponses()

    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "unit-test-key")
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeClient))
    with pytest.raises(LLMGenerationError, match="answer-generation service could not complete"):
        maybe_call_llm("Prompt", "Context")


def test_openai_incomplete_answer_is_rejected(monkeypatch) -> None:
    class FakeResponses:
        def create(self, **kwargs):
            return SimpleNamespace(
                output_text="Partial answer",
                status="incomplete",
                incomplete_details={"reason": "max_output_tokens"},
            )

    class FakeClient:
        responses = FakeResponses()

    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "unit-test-key")
    monkeypatch.setattr(project_agents, "_openai_client", lambda *args: FakeClient())
    with pytest.raises(LLMGenerationError, match="incomplete answer"):
        maybe_call_llm("Prompt", "Context")


def test_azure_requires_deployment_name(monkeypatch) -> None:
    monkeypatch.setenv("AI_PROVIDER", "azure")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "unit-test-key")
    monkeypatch.delenv("AZURE_OPENAI_DEPLOYMENT", raising=False)
    assert settings.ai_provider_status()["status"] == "unavailable"
    with pytest.raises(LLMConfigurationError, match="AZURE_OPENAI_DEPLOYMENT is not configured"):
        maybe_call_llm("Prompt", "Context")


def test_ollama_provider_checks_local_model_without_downloading(monkeypatch) -> None:
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "llama3.2:3b")
    calls: list[tuple[str, object]] = []

    def installed_model(url: str, headers=None) -> dict:
        calls.append((url, headers))
        return {"models": [{"name": "llama3.2:3b"}]}

    monkeypatch.setattr(settings, "_get_json", installed_model)
    status = settings.ai_provider_status(check_connectivity=True)
    assert status["configured"] is True
    assert status["status"] == "operational"
    assert status["operational"] is True
    assert calls == [("http://localhost:11434/api/tags", None)]

    monkeypatch.setattr(settings, "_get_json", lambda url, headers=None: {"models": []})
    missing_model = settings.ai_provider_status(check_connectivity=True)
    assert missing_model["status"] == "unavailable"
    assert missing_model["connectivity"] == "reachable"
    assert "not installed" in missing_model["message"]
    assert "download" in missing_model["message"]


def test_openai_status_rejects_placeholder_and_checks_model_without_exposing_key(monkeypatch) -> None:
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "your-openai-api-key")
    assert settings.ai_provider_status(check_connectivity=True)["configured"] is False
    with pytest.raises(LLMConfigurationError, match="OPENAI_API_KEY is not configured"):
        maybe_call_llm("Prompt", "Context")

    monkeypatch.setenv("OPENAI_API_KEY", "unit-test-key")
    calls: list[tuple[str, dict[str, str] | None]] = []

    def available_model(url: str, headers=None) -> dict:
        calls.append((url, headers))
        return {"data": [{"id": "gpt-4o-mini"}]}

    monkeypatch.setattr(settings, "_get_json", available_model)
    status = settings.ai_provider_status(check_connectivity=True)
    assert status["status"] == "operational"
    assert calls[0][0] == "https://api.openai.com/v1/models"
    assert calls[0][1] == {"Authorization": "Bearer unit-test-key"}
    assert "unit-test-key" not in str(status)


def test_ollama_generation_passes_grounded_messages_to_existing_provider_service(monkeypatch) -> None:
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "local-model:latest")
    monkeypatch.setenv("LLM_REQUEST_TIMEOUT_SECONDS", "23")
    observed: dict[str, object] = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"message": {"content": "Grounded local answer [S1]."}}

    class FakeClient:
        def post(self, url, json):
            observed["url"] = url
            observed["body"] = json
            return FakeResponse()

    monkeypatch.setattr(project_agents, "_ollama_http_client", lambda base_url, timeout: (
        observed.update({"base_url": base_url, "timeout": timeout}) or FakeClient()
    ))
    monkeypatch.setattr(settings, "_get_json", lambda *args, **kwargs: pytest.fail(
        "Chat generation should not perform an extra model-list preflight."
    ))
    answer = maybe_call_llm(
        "Use only cited evidence.",
        "[S1] Source: plan.txt\nEvidence: Launch is scheduled for Friday.",
    )
    assert answer == "Grounded local answer [S1]."
    assert observed["base_url"] == "http://127.0.0.1:11434"
    assert observed["timeout"] == 23
    assert observed["url"] == "/api/chat"
    body = observed["body"]
    assert body["model"] == "local-model:latest"
    assert body["stream"] is False
    assert body["keep_alive"] == "10m"
    assert body["options"]["temperature"] == 0
    assert body["options"]["num_ctx"] == 4096
    assert body["options"]["num_predict"] == 192
    assert body["messages"][0] == {"role": "system", "content": "Use only cited evidence."}
    assert "Launch is scheduled for Friday" in body["messages"][1]["content"]


def test_ollama_generation_reports_unavailable_model_from_chat_response(monkeypatch) -> None:
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "missing-model:latest")
    chat_requested = False

    class FakeResponse:
        def raise_for_status(self):
            request = httpx.Request("POST", "http://127.0.0.1:11434/api/chat")
            response = httpx.Response(404, request=request)
            raise httpx.HTTPStatusError("model not found", request=request, response=response)

    class FakeClient:
        def post(self, url, json):
            nonlocal chat_requested
            chat_requested = True
            return FakeResponse()

    monkeypatch.setattr(project_agents, "_ollama_http_client", lambda *args: FakeClient())
    with pytest.raises(LLMGenerationError, match="answer-generation service could not complete"):
        maybe_call_llm("Prompt", "Context")
    assert chat_requested is True


def test_ollama_request_timeout_is_reported_as_timeout(monkeypatch) -> None:
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "local-model:latest")

    class FakeClient:
        def post(self, url, json):
            raise httpx.ReadTimeout("local model did not respond")

    monkeypatch.setattr(project_agents, "_ollama_http_client", lambda *args: FakeClient())
    with pytest.raises(LLMTimeoutError, match="timed out after"):
        maybe_call_llm("Prompt", "Context")


def test_ollama_truncated_answer_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "local-model:latest")

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "done_reason": "length",
                "message": {"content": "Partial answer"},
            }

    class FakeClient:
        def post(self, url, json):
            return FakeResponse()

    monkeypatch.setattr(project_agents, "_ollama_http_client", lambda *args: FakeClient())
    with pytest.raises(LLMGenerationError, match="incomplete answer"):
        maybe_call_llm("Prompt", "Context")


def test_azure_status_checks_deployment_without_returning_api_key(monkeypatch) -> None:
    monkeypatch.setenv("AI_PROVIDER", "azure")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "unit-test-key")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "project-chat")
    calls: list[tuple[str, dict[str, str] | None]] = []

    def available_deployment(url: str, headers=None) -> dict:
        calls.append((url, headers))
        return {"id": "project-chat", "status": "succeeded"}

    monkeypatch.setattr(settings, "_get_json", available_deployment)
    status = settings.ai_provider_status(check_connectivity=True)
    assert status["status"] == "operational"
    assert calls == [(
        "https://example.openai.azure.com/openai/deployments/project-chat?api-version=2024-02-01",
        {"api-key": "unit-test-key"},
    )]
    assert "unit-test-key" not in str(status)


def test_root_and_health_report_service_and_ai_configuration_without_secrets(tmp_path, monkeypatch) -> None:
    _configure_isolated_platform(tmp_path, monkeypatch)
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert settings.env_file == settings.project_root / ".env"

    with TestClient(app) as client:
        root = client.get("/")
        assert root.status_code == 200
        assert root.json() == {
            "application": "Enterprise Project Intelligence Platform",
            "version": "1.0.0",
            "status": "healthy",
            "api_status": "online",
            "health_url": "/health",
            "docs_url": "/docs",
        }

        assert client.get("/docs").status_code == 200
        openapi = client.get("/openapi.json")
        assert openapi.status_code == 200
        assert {
            "/assistant/chat",
            "/assistant/conversations/{session_id}",
            "/documents",
            "/search",
        }.issubset(openapi.json()["paths"])
        assert client.get("/documents").status_code == 200
        assert client.get("/assistant/conversations/" + str(uuid4())).status_code == 200

        health = client.get("/health")
        assert health.status_code == 200
        body = health.json()
        assert body["database"] == "healthy"
        assert body["vector_store"] == "healthy"
        assert body["ai_provider"]["provider"] == "openai"
        assert body["ai_provider"]["status"] == "unavailable"
        assert body["ai_provider"]["configured"] is False
        assert "OPENAI_API_KEY" in body["ai_provider"]["message"]
        assert "unit-test-key" not in health.text

        monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
        monkeypatch.setattr(
            settings,
            "_get_json",
            lambda url, headers=None: {"data": [{"id": "gpt-4o-mini"}]},
        )
        configured = client.get("/health")
        assert configured.json()["ai_provider"]["status"] == "operational"
        assert configured.json()["ai_provider"]["connectivity"] == "reachable"
        assert "test-secret" not in configured.text

    vector_store.reset_client()


def _configure_isolated_platform(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "platform-data"))
    monkeypatch.setenv("CHUNK_SIZE_TOKENS", "200")
    monkeypatch.setenv("CHUNK_OVERLAP_TOKENS", "10")
    monkeypatch.setattr(pipeline, "embed_texts", lambda texts: [[1.0, 0.0, 0.0] for _ in texts])
    monkeypatch.setattr(search, "embed_texts", lambda texts: [[1.0, 0.0, 0.0] for _ in texts])
    monkeypatch.setattr(assistant, "embed_texts", lambda texts: [[1.0, 0.0, 0.0] for _ in texts])
    monkeypatch.setattr(
        assistant,
        "maybe_call_llm",
        lambda prompt, context: "Answer: I found source-backed project evidence. [S1]",
    )
    vector_store.reset_client()


def _upload_project_documents(client: TestClient) -> list[str]:
    response = client.post("/documents/upload", files=[
        (
            "files",
            (
                "attendance-plan.txt",
                b"Project goal: Deliver an attendance platform.\n"
                b"The primary deliverable is an attendance dashboard.\n"
                b"Milestone 1 is due 2099-12-01.\n"
                b"Students requested a way to view their attendance so that they can track attendance.\n"
                b"User requirement: As a project user, I want to export reports so that I can share project status.\n"
                b"Action item: Ravi must provide the payment API by Friday.\n"
                b"Risk: The payment API is delayed and may delay frontend integration.\n"
                b"Risk: The authentication dependency might delay release; likelihood: likely; impact: high.\n"
                b"Pending decision: The product manager must decide the payment flow.\n"
                b"Alice is responsible for acceptance testing.\n"
                b"Action item: Review the attendance dashboard.\n"
                b"Acceptance criteria: The attendance percentage must appear on the dashboard.\n",
                "text/plain",
            ),
        ),
        (
            "files",
            (
                "meeting-followup.txt",
                b"The frontend team is waiting for infrastructure sign-off.\n"
                b"The payment API is still not available. The frontend team is waiting for it.\n"
                b"The backend team will provide the API by Friday.\n"
                b"The product manager needs to approve the payment flow.\n"
                b"The team must review the release checklist.",
                "text/plain",
            ),
        ),
    ])
    assert response.status_code == 200
    results = response.json()["results"]
    assert all(item["status"] == "indexed" for item in results)
    return [item["document_id"] for item in results]


def test_project_insights_dashboard_metrics_match_agents_and_current_documentation(
    tmp_path, monkeypatch,
) -> None:
    _configure_isolated_platform(tmp_path, monkeypatch)
    with TestClient(app) as client:
        empty = client.get("/project-insights")
        assert empty.status_code == 200
        empty_body = empty.json()
        assert empty_body["metrics"] == {
            "total_uploaded_documents": 0,
            "processed_documents": 0,
            "current_indexed_documents": 0,
            "total_risks": 0,
            "total_blockers": 0,
            "total_actions": 0,
            "total_deliverables": 0,
            "overall_project_health_score": None,
            "documentation_generated_items": 0,
        }
        assert empty_body["documentation"]["is_current"] is False
        assert empty_body["risk_counts"] == {
            "Critical": 0, "High": 0, "Medium": 0, "Low": 0, "Unrated": 0,
        }
        assert empty_body["milestone_counts"]["total"] == 0

        document_ids = _upload_project_documents(client)
        scope = client.post("/analysis/scope", json={}).json()
        risks = client.post("/analysis/risks", json={}).json()
        blockers = client.post("/analysis/blockers", json={}).json()
        generation = client.post(
            "/documentation/generate",
            json={"document_ids": document_ids},
        ).json()
        health = client.post("/project-health/recalculate").json()

        response = client.get("/project-insights")
        assert response.status_code == 200
        body = response.json()
        stats = client.get("/stats").json()

        assert body["metrics"]["total_uploaded_documents"] == stats["total_documents"]
        assert body["metrics"]["processed_documents"] == stats["successful_documents"]
        assert body["metrics"]["current_indexed_documents"] == health["document_count"]
        assert body["metrics"]["total_risks"] == len(risks["findings"]) == len(body["risks"])
        assert body["metrics"]["total_blockers"] == len(blockers["blockers"])
        assert body["metrics"]["total_actions"] == len(body["actions"]) == body["action_counts"]["total"]
        assert body["metrics"]["total_deliverables"] == len(scope["deliverables"])
        assert body["metrics"]["overall_project_health_score"] == health["overall_score"]
        assert body["blockers"] == blockers["blockers"]
        assert body["documentation"]["is_current"] is True
        assert body["documentation"]["generation_id"] == generation["id"]
        assert body["documentation"]["counts"] == {
            "user_stories": len(generation["user_stories"]),
            "risk_register": len(generation["risk_register"]),
            "action_items": len(generation["action_items"]),
            "pending_decisions": len(generation["pending_decisions"]),
        }
        assert body["metrics"]["documentation_generated_items"] == generation["total_generated_items"]
        assert sum(body["risk_counts"].values()) == body["metrics"]["total_risks"]
        assert (
            body["blocker_counts"]["unresolved"] + body["blocker_counts"]["resolved"]
            == body["blocker_counts"]["total"]
        )
        assert sum(
            count for key, count in body["milestone_counts"].items() if key != "total"
        ) == body["milestone_counts"]["total"]

    vector_store.reset_client()


def test_end_to_end_milestones_use_incremental_documents_through_insights_and_rag(
    tmp_path, monkeypatch,
) -> None:
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "platform-data"))
    monkeypatch.setenv("CHUNK_SIZE_TOKENS", "200")
    monkeypatch.setenv("CHUNK_OVERLAP_TOKENS", "10")
    embedding_batches = []

    def fake_embed(texts):
        embedding_batches.append(list(texts))
        return [[1.0, 0.0, 0.0] for _ in texts]

    monkeypatch.setattr(pipeline, "embed_texts", fake_embed)
    monkeypatch.setattr(search, "embed_texts", lambda texts: [[1.0, 0.0, 0.0] for _ in texts])
    monkeypatch.setattr(assistant, "embed_texts", lambda texts: [[1.0, 0.0, 0.0] for _ in texts])
    monkeypatch.setattr(
        assistant,
        "maybe_call_llm",
        lambda prompt, context: "The answer is supported by the retrieved project evidence. [S1]",
    )
    vector_store.reset_client()

    with TestClient(app) as client:
        initial_upload = client.post("/documents/upload", files=[
            (
                "files",
                (
                    "project-plan.txt",
                    b"Project goal: Deliver an analytics platform.\n"
                    b"The primary deliverable is an analytics dashboard.\n"
                    b"Milestone 1 is due 2099-12-01 and is in progress.\n"
                    b"Students requested the ability to view project forecasts so that they can plan delivery.\n"
                    b"Risk: The payment API is delayed and may delay frontend integration.\n"
                    b"Action item: Ravi must provide the payment API by 2099-11-30.\n"
                    b"Blocker: The team is waiting for security approval.\n"
                    b"Pending decision: The product manager must decide the payment flow.",
                    "text/plain",
                ),
            ),
            (
                "files",
                (
                    "milestones.csv",
                    b"Project,Milestone,Due Date,Status\nAtlas,Data migration,2099-12-15,Pending\n",
                    "text/csv",
                ),
            ),
        ])
        initial_results = initial_upload.json()["results"]
        initial_ids = [item["document_id"] for item in initial_results]
        assert all(item["status"] == "indexed" for item in initial_results)
        assert len(embedding_batches) == 2
        assert all(client.get(f"/documents/{document_id}").json()["processing_status"] == "indexed"
                   for document_id in initial_ids)
        initial_vector_ids = set(vector_store.get_collection().get()["ids"])
        assert initial_vector_ids

        incremental = client.post("/documents/upload", files=[
            (
                "files",
                (
                    "integration-notes.md",
                    b"Risk: The Kestrel integration has no approval and will block release.\n"
                    b"Action item: Alice must validate the secure launch checklist by 2099-12-20.",
                    "text/markdown",
                ),
            ),
        ]).json()["results"][0]
        assert incremental["status"] == "indexed"
        assert len(embedding_batches) == 3
        current_vector_ids = set(vector_store.get_collection().get()["ids"])
        assert initial_vector_ids.issubset(current_vector_ids)
        all_document_ids = [*initial_ids, incremental["document_id"]]

        duplicate = client.post("/documents/upload", files=[
            (
                "files",
                (
                    "integration-notes-copy.md",
                    b"Risk: The Kestrel integration has no approval and will block release.\n"
                    b"Action item: Alice must validate the secure launch checklist by 2099-12-20.",
                    "text/markdown",
                ),
            ),
        ]).json()["results"][0]
        assert duplicate["duplicate"] is True
        assert duplicate["document_id"] == incremental["document_id"]
        assert len(embedding_batches) == 3
        assert set(vector_store.get_collection().get()["ids"]) == current_vector_ids

        searchable = client.post("/search", json={
            "query": "analytics project Kestrel integration",
            "top_k": 50,
        }).json()["results"]
        assert set(all_document_ids).issubset({item["document_id"] for item in searchable})

        scope = client.post("/analysis/scope", json={"document_ids": all_document_ids}).json()
        risks = client.post("/analysis/risks", json={"document_ids": all_document_ids}).json()
        blockers = client.post("/analysis/blockers", json={"document_ids": all_document_ids}).json()
        assert scope["deliverables"]
        assert risks["findings"]
        assert blockers["blockers"] and blockers["action_items"] and blockers["pending_decisions"]

        generation_response = client.post(
            "/documentation/generate",
            json={"document_ids": all_document_ids},
        )
        assert generation_response.status_code == 200
        generation = generation_response.json()
        assert generation["user_stories"] and generation["risk_register"] and generation["action_items"]

        health_response = client.post("/project-health/recalculate")
        assert health_response.status_code == 200
        health = health_response.json()
        assert health["document_count"] == len(all_document_ids)

        for question, expected_document_id in (
            ("What is the analytics dashboard deliverable?", initial_ids[0]),
            ("What blocks the Kestrel integration?", incremental["document_id"]),
        ):
            answer = client.post("/assistant/chat", json={
                "session_id": "bd2ad170-e6a8-4bdb-b7a1-2db363e55c3b",
                "question": question,
            })
            assert answer.status_code == 200
            assert answer.json()["sources"]
            assert expected_document_id in {
                source["document_id"] for source in answer.json()["sources"]
            }

        insights_response = client.get("/project-insights")
        assert insights_response.status_code == 200
        insights = insights_response.json()
        stats = client.get("/stats").json()
        assert insights["metrics"]["total_uploaded_documents"] == stats["total_documents"] == 3
        assert insights["metrics"]["processed_documents"] == stats["successful_documents"] == 3
        assert insights["metrics"]["total_risks"] == len(risks["findings"])
        assert insights["metrics"]["total_blockers"] == len(blockers["blockers"])
        assert insights["metrics"]["total_actions"] == len(insights["actions"])
        assert insights["metrics"]["total_deliverables"] == len(scope["deliverables"])
        assert insights["metrics"]["overall_project_health_score"] == health["overall_score"]
        assert insights["documentation"]["is_current"] is True
        assert insights["documentation"]["generation_id"] == generation["id"]
        assert sum(insights["risk_counts"].values()) == len(insights["risks"])
        assert insights["blocker_counts"]["total"] == len(insights["blockers"])
        assert insights["action_counts"]["total"] == len(insights["actions"])
        assert insights["milestone_counts"]["total"] == len(insights["scope"]["milestones"])

        assert client.delete(f"/documents/{incremental['document_id']}").status_code == 200
        after_delete = client.post("/search", json={
            "query": "Kestrel integration approval release",
            "top_k": 50,
        }).json()["results"]
        assert all(item["document_id"] != incremental["document_id"] for item in after_delete)

    vector_store.reset_client()


def test_documentation_generation_is_grounded_persistent_and_exportable(tmp_path, monkeypatch) -> None:
    _configure_isolated_platform(tmp_path, monkeypatch)
    with TestClient(app) as client:
        document_ids = _upload_project_documents(client)
        response = client.post("/documentation/generate", json={"document_ids": document_ids})
        assert response.status_code == 200
        result = response.json()

        assert result["total_generated_items"] > 0
        assert len(result["source_documents"]) == 2
        assert result["user_stories"]
        story = next(item for item in result["user_stories"] if "attendance" in item["requirement"].lower())
        assert all(item["source_document"] == "attendance-plan.txt" for item in result["user_stories"])
        assert story["user_role"].lower() == "students"
        assert "view their attendance" in story["description"]
        assert story["evidence"]["filename"] == "attendance-plan.txt"
        assert "Students requested" in story["evidence"]["excerpt"]
        assert story["acceptance_criteria"]

        assert result["risk_register"]
        risk = next(item for item in result["risk_register"] if "payment API" in item["description"])
        assert risk["category"] in {"Schedule", "Dependency", "Technical"}
        assert risk["likelihood"] == "Not stated in source"
        assert risk["evidence"]["document_id"] in document_ids
        assert risk["evidence"]["excerpt"]
        explicit_risk = next(item for item in result["risk_register"] if "authentication dependency" in item["description"])
        assert explicit_risk["likelihood"] == "Likely"
        assert explicit_risk["impact"] == "High"
        unavailable_risk = next(item for item in result["risk_register"] if "payment API is still not available" in item["description"])
        assert unavailable_risk["inference"] is True
        assert unavailable_risk["category"] == "Dependency"

        action = next(item for item in result["action_items"] if "Ravi must provide" in item["description"])
        assert action["assignee"] == "Ravi"
        assert action["due_date"] == "Friday"
        assert action["status"] == "Not stated"
        assert action["evidence"]["filename"] == "attendance-plan.txt"
        no_owner = next(item for item in result["action_items"] if "Review the attendance dashboard" in item["description"])
        assert no_owner["assignee"] is None
        assert no_owner["due_date"] is None
        team_action = next(item for item in result["action_items"] if "backend team will provide" in item["description"])
        assert team_action["assignee"].lower() == "backend team"
        assert team_action["due_date"] == "Friday"
        decision = next(item for item in result["pending_decisions"] if "payment flow" in item["description"])
        assert decision["evidence"]["document_id"] in document_ids
        assert decision["responsible_person"].lower() == "product manager"

        repeated = client.post("/documentation/generate", json={"document_ids": document_ids}).json()
        assert repeated["id"] == result["id"]
        with connect() as connection:
            generation_count = connection.execute(
                "SELECT COUNT(*) FROM documentation_generations"
            ).fetchone()[0]
        assert generation_count == 1
        saved = client.get("/documentation/latest", params=[("document_ids", item) for item in document_ids])
        assert saved.status_code == 200 and saved.json()["id"] == result["id"]
        assert client.get(f"/documentation/{result['id']}").json()["total_generated_items"] == result["total_generated_items"]
        assert client.get(f"/documentation/{result['id']}/export", params={"format": "json"}).headers["content-type"].startswith("application/json")
        csv_export = client.get(f"/documentation/{result['id']}/export", params={"format": "csv"})
        assert csv_export.status_code == 200
        assert "user_story" in csv_export.text and "evidence_excerpt" in csv_export.text

    vector_store.reset_client()


def test_health_scores_use_weights_and_missing_evidence_is_not_perfect(tmp_path, monkeypatch) -> None:
    _configure_isolated_platform(tmp_path, monkeypatch)
    with TestClient(app) as client:
        assert client.post("/documentation/generate", json={}).status_code == 422
        no_documents = client.post("/project-health/recalculate")
        assert no_documents.status_code == 200
        assert no_documents.json()["classification"] == "Insufficient Data"
        assert no_documents.json()["overall_score"] is None
        assert client.get("/project-health/latest").json()["id"] == no_documents.json()["id"]

        document_ids = _upload_project_documents(client)
        result = client.post("/project-health/recalculate").json()
        assert result["document_count"] == 2
        assert result["dimensions"]["scope_clarity"]["score"] is not None
        assert result["dimensions"]["timeline_health"]["score"] is not None
        assert result["dimensions"]["blocker_health"]["score"] is not None
        expected = round(
            result["dimensions"]["scope_clarity"]["score"] * 0.35
            + result["dimensions"]["timeline_health"]["score"] * 0.35
            + result["dimensions"]["blocker_health"]["score"] * 0.30,
            1,
        )
        assert result["overall_score"] == expected
        assert result["weights"] == {
            "scope_clarity": 0.35,
            "timeline_health": 0.35,
            "blocker_health": 0.3,
        }
        assert result["classification"] in {"Healthy", "Needs Attention", "At Risk"}
        assert result["identified_risks"]
        assert result["unresolved_blockers"] or result["pending_decisions"]
        assert client.get("/project-health/latest").json()["id"] == result["id"]

        before = result["id"]
        added = client.post("/documents/upload", files=[
            ("files", ("extra-scope.txt", b"Objective: document the reporting workflow.", "text/plain")),
        ])
        assert added.json()["successful"] == 1
        recalculated = client.post("/project-health/recalculate").json()
        assert recalculated["document_count"] == 3
        assert recalculated["id"] != before
        assert "20 points" in recalculated["dimensions"]["scope_clarity"]["explanation"]

    vector_store.reset_client()


def test_blocker_health_does_not_infer_zero_from_unmentioned_or_contradictory_evidence(tmp_path, monkeypatch) -> None:
    _configure_isolated_platform(tmp_path, monkeypatch)
    with TestClient(app) as client:
        response = client.post("/documents/upload", files=[
            ("files", ("empty-status.txt", b"Project goal: track the reporting work.", "text/plain")),
        ])
        assert response.json()["successful"] == 1
        result = client.post("/project-health/recalculate").json()
        dimension = result["dimensions"]["blocker_health"]
        assert dimension["score"] is None
        assert not dimension["available"]

        clear_status = client.post("/documents/upload", files=[
            ("files", ("clear-status.txt", b"The project currently has no blockers.", "text/plain")),
        ])
        assert clear_status.json()["successful"] == 1
        result = client.post("/project-health/recalculate").json()
        assert result["dimensions"]["blocker_health"]["score"] == 100
        assert any("explicitly reports no unresolved blockers" in factor
                   for factor in result["dimensions"]["blocker_health"]["factors"])

    vector_store.reset_client()


def test_assistant_retrieves_source_evidence_keeps_session_history_and_handles_unknowns(tmp_path, monkeypatch) -> None:
    _configure_isolated_platform(tmp_path, monkeypatch)
    session_id = str(uuid4())
    with TestClient(app) as client:
        document_ids = _upload_project_documents(client)
        first = client.post("/assistant/chat", json={
            "session_id": session_id,
            "question": "What action is assigned to Ravi?",
            "document_ids": [document_ids[0]],
            "top_k": 6,
        })
        assert first.status_code == 200
        first_result = first.json()
        assert first_result["answer_mode"] == "llm"
        assert "source-backed project evidence" in first_result["answer"]
        assert first_result["sources"]
        assert any(source["filename"] == "attendance-plan.txt" for source in first_result["sources"])
        assert any(source["document_id"] in document_ids for source in first_result["sources"])
        assert all(source["document_id"] == document_ids[0] for source in first_result["sources"])
        assert any(source["excerpt"] for source in first_result["sources"])

        follow_up = client.post("/assistant/chat", json={
            "session_id": session_id,
            "question": "When is it due?",
        })
        assert follow_up.status_code == 200
        assert follow_up.json()["sources"]
        history = client.get(f"/assistant/conversations/{session_id}").json()["messages"]
        assert len(history) == 4
        assert history[0]["role"] == "user"
        assert history[1]["role"] == "assistant"
        assert history[1]["sources"]
        assert history[1]["answer_mode"] == "llm"

        observed: dict[str, str] = {}
        def fake_llm(prompt: str, context: str) -> str:
            observed["prompt"] = prompt
            observed["context"] = context
            return "Answer: The project goal is documented in [S1]."
        monkeypatch.setattr(assistant, "maybe_call_llm", fake_llm)
        generated = client.post("/assistant/chat", json={
            "session_id": str(uuid4()),
            "question": "What is the project goal?",
        })
        assert generated.status_code == 200
        assert generated.json()["answer_mode"] == "llm"
        assert "attendance-plan.txt, line 1" in generated.json()["answer"]
        assert "[S1]" not in generated.json()["answer"]
        assert "Use only supplied evidence for facts" in observed["prompt"]
        assert "attendance-plan.txt" in observed["context"]
        assert "line start 1" in observed["context"]
        assert generated.json()["sources"][0]["document_id"] not in observed["context"]
        assert generated.json()["sources"][0]["chunk_id"] not in observed["context"]
        health_answer = client.post("/assistant/chat", json={
            "session_id": str(uuid4()),
            "question": "Are we on track?",
        }).json()
        assert health_answer["answer_mode"] == "health_analysis"
        assert health_answer["health"]["classification"] in {
            "Healthy", "Needs Attention", "At Risk", "Insufficient Data",
        }
        assert health_answer["health"]["classification"] in health_answer["answer"]
        if health_answer["health"]["overall_score"] is not None:
            assert f"{health_answer['health']['overall_score']:.1f}/100" in health_answer["answer"]
        assert "Health breakdown (platform calculation)" in health_answer["answer"]

        no_evidence_session = str(uuid4())
        monkeypatch.setattr(assistant, "query_vectors", lambda *args, **kwargs: [])
        missing = client.post("/assistant/chat", json={
            "session_id": no_evidence_session,
            "question": "What is the project team's favorite dessert?",
        })
        assert missing.status_code == 200
        assert missing.json()["answer_mode"] == "insufficient_evidence"
        assert "could not find relevant evidence" in missing.json()["answer"]

        cleared = client.delete(f"/assistant/conversations/{session_id}")
        assert cleared.status_code == 200
        assert client.get(f"/assistant/conversations/{session_id}").json()["messages"] == []

    vector_store.reset_client()


def test_assistant_reports_missing_provider_configuration_instead_of_fallback(tmp_path, monkeypatch) -> None:
    _configure_isolated_platform(tmp_path, monkeypatch)
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(assistant, "maybe_call_llm", configured_llm)
    with TestClient(app) as client:
        _upload_project_documents(client)
        response = client.post("/assistant/chat", json={
            "session_id": str(uuid4()),
            "question": "What is the attendance project goal?",
        })
        assert response.status_code == 503
        assert "OPENAI_API_KEY is not configured" in response.json()["detail"]
        assert "fallback" not in response.json()["detail"].lower()
    vector_store.reset_client()


def test_assistant_llm_timeout_returns_gateway_timeout(tmp_path, monkeypatch) -> None:
    _configure_isolated_platform(tmp_path, monkeypatch)

    def timeout_llm(prompt: str, context: str) -> str:
        raise LLMTimeoutError("The configured Ollama answer-generation service timed out after 1 seconds.")

    monkeypatch.setattr(assistant, "maybe_call_llm", timeout_llm)
    with TestClient(app) as client:
        _upload_project_documents(client)
        response = client.post("/assistant/chat", json={
            "session_id": str(uuid4()),
            "question": "What is the attendance project goal?",
        })
    assert response.status_code == 504
    assert response.json()["detail"]["code"] == "llm_timeout"
    assert "timed out" in response.json()["detail"]["message"]
    vector_store.reset_client()


def test_backend_startup_warms_embedding_model_for_indexed_documents(tmp_path, monkeypatch) -> None:
    from app import main as main_module

    _configure_isolated_platform(tmp_path, monkeypatch)
    from app.database.db import initialize_database

    initialize_database()
    with connect() as connection:
        connection.execute(
            """INSERT INTO documents
               (id, filename, stored_filename, file_type, file_size, sha256, uploaded_at,
                created_at, updated_at, processing_status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "indexed-document",
                "plan.txt",
                "plan.txt",
                "txt",
                1,
                "a" * 64,
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00",
                "indexed",
            ),
        )
    warmed = []
    monkeypatch.setattr(main_module, "get_model", lambda: warmed.append(True))
    with TestClient(app):
        pass
    assert warmed == [True]
    vector_store.reset_client()
