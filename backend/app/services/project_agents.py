import json
import logging
import re
import uuid
from functools import lru_cache
from datetime import datetime, timezone
from time import perf_counter
from typing import Any
from urllib.parse import urlparse

import httpx
from app.core.config import settings
from app.database.db import connect
from app.schemas.analysis import (
    AnalysisRequest,
    BlockerAnalysis,
    BlockerItem,
    EvidenceReference,
    RiskAnalysis,
    RiskFinding,
    ScopeAnalysis,
    ScopeDependency,
    ScopeFinding,
    ScopeMilestone,
    ScopeResponsibility,
)

logger = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip()


def _sentence_split(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [item.strip() for item in parts if item and item.strip()]


def _date_like(value: str | None) -> str | None:
    if not value:
        return None
    normalized = _normalize_text(value)
    if not normalized:
        return None
    return normalized


def _find_dates(text: str) -> list[str]:
    patterns = [
        r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},\s+\d{4}\b",
        r"\b\d{4}-\d{2}-\d{2}\b",
        r"\b\d{1,2}/\d{1,2}/\d{2,4}\b",
        r"\bQ[1-4]\s*\d{4}\b",
        r"\b(?:FY|fiscal\s+year)\s*\d{4}\b",
        r"\b(?:Phase|Sprint|Milestone)\s*\d+\b",
        r"\b(?:by|due(?:\s+on)?|before|until)\s+(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b",
    ]
    found: list[str] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            value = match.group(1) if match.lastindex else match.group(0)
            if value not in found:
                found.append(value)
    return found


def _build_reference(
    document: dict[str, Any],
    excerpt: str,
    chunks: list[dict[str, Any]] | None = None,
) -> EvidenceReference:
    source_excerpt = excerpt.strip()
    selected_chunk = None
    actual_excerpt = source_excerpt
    source_words = source_excerpt.split()
    if source_words and chunks:
        for word_count in range(len(source_words), 3, -1):
            pattern = r"\s+".join(re.escape(part) for part in source_words[:word_count])
            for chunk in chunks:
                match = re.search(pattern, chunk.get("text") or "", flags=re.IGNORECASE)
                if match:
                    selected_chunk = chunk
                    actual_excerpt = match.group(0)
                    break
            if selected_chunk:
                break
    location = selected_chunk.get("location", {}) if selected_chunk else {}
    return EvidenceReference(
        document_id=document.get("id"),
        filename=document.get("filename"),
        chunk_id=selected_chunk.get("id") if selected_chunk else None,
        excerpt=actual_excerpt[:800],
        location=location,
        source=document.get("filename"),
    )


def _structured_deliverable_milestones(
    document: dict[str, Any],
    chunks: list[dict[str, Any]],
) -> list[ScopeMilestone]:
    milestones = []
    for block in re.split(r"\n\s*\n", document.get("extracted_text") or ""):
        fields = {
            key.casefold(): value.strip()
            for key, value in re.findall(
                r"(?im)^\s*(Deliverable|Due date|Status):\s*(.*?)\s*$",
                block,
            )
        }
        due_dates = _find_dates(fields.get("due date", ""))
        name = fields.get("deliverable")
        if not name or not due_dates:
            continue
        status = fields.get("status") or "not stated"
        description = f"{name}; due {due_dates[0]}; status {status}"
        reference = _build_reference(document, block, chunks)
        milestones.append(ScopeMilestone(
            name=name,
            date=due_dates[0],
            status=status,
            description=description,
            source=document.get("filename"),
            evidence=[reference] if reference.excerpt else [],
        ))
    return milestones


def _load_documents(document_id: str | None = None, document_ids: list[str] | None = None) -> list[dict[str, Any]]:
    if document_id and document_ids:
        document_ids = [*dict.fromkeys([document_id, *document_ids])]
    elif document_id:
        document_ids = [document_id]
    if document_ids and not all(item for item in document_ids):
        document_ids = [item for item in document_ids if item]
    with connect() as connection:
        if document_ids:
            placeholders = ", ".join("?" for _ in document_ids)
            rows = connection.execute(
                f"""SELECT * FROM documents WHERE id IN ({placeholders})
                    AND processing_status = 'indexed' AND is_current_version = 1
                    ORDER BY uploaded_at DESC""",
                document_ids,
            ).fetchall()
        elif document_id:
            rows = connection.execute(
                """SELECT * FROM documents WHERE id = ? AND processing_status = 'indexed'
                   AND is_current_version = 1 ORDER BY uploaded_at DESC""",
                (document_id,),
            ).fetchall()
        else:
            rows = connection.execute(
                """SELECT * FROM documents WHERE processing_status = 'indexed'
                   AND is_current_version = 1 ORDER BY uploaded_at DESC"""
            ).fetchall()
    return [dict(row) for row in rows]


def _load_chunks_for_document(document_id: str) -> list[dict[str, Any]]:
    with connect() as connection:
        rows = connection.execute(
            "SELECT id, document_id, chunk_index, text, location_json FROM chunks WHERE document_id = ? ORDER BY chunk_index ASC",
            (document_id,),
        ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        payload = dict(row)
        payload["location"] = json.loads(payload.get("location_json") or "{}")
        result.append(payload)
    return result


def _collect_evidence(document: dict[str, Any], sentences: list[str]) -> list[EvidenceReference]:
    chunks = _load_chunks_for_document(document["id"])
    evidence: list[EvidenceReference] = []
    for sentence in sentences:
        reference = _build_reference(document, sentence, chunks)
        if reference.chunk_id:
            evidence.append(reference)
    return evidence


def _persist_analysis(analysis_type: str, document_ids: list[str], payload: dict[str, Any]) -> str:
    record_id = str(uuid.uuid4())
    with connect() as connection:
        connection.execute(
            "INSERT INTO analysis_results (id, analysis_type, document_ids, created_at, result_json) VALUES (?, ?, ?, ?, ?)",
            (record_id, analysis_type, json.dumps(document_ids, ensure_ascii=False), _utc_now(), json.dumps(payload, ensure_ascii=False)),
        )
    return record_id


def analyze_scope(
    document_id: str | None = None,
    document_ids: list[str] | None = None,
    query: str | None = None,
    top_k: int = 5,
    persist: bool = True,
) -> dict[str, Any]:
    documents = _load_documents(document_id=document_id, document_ids=document_ids)
    if not documents:
        payload = ScopeAnalysis(
            missing_information=["No indexed documents are available for scope extraction."],
            source_documents=[],
        ).model_dump(mode="json")
        if persist:
            _persist_analysis("scope", [], payload)
        return payload

    goals: list[str] = []
    objectives: list[str] = []
    deliverables: list[str] = []
    milestones: list[ScopeMilestone] = []
    responsibilities: list[ScopeResponsibility] = []
    dependencies: list[ScopeDependency] = []
    progress: list[str] = []
    findings: list[ScopeFinding] = []
    missing_information: list[str] = []
    source_documents: list[str] = []
    notes: list[str] = []

    for document in documents:
        source_documents.append(document.get("filename") or document.get("id") or "unknown")
        text = document.get("extracted_text") or ""
        sentences = _sentence_split(text)
        chunks = _load_chunks_for_document(document["id"])
        for milestone in _structured_deliverable_milestones(document, chunks):
            milestones.append(milestone)
            findings.append(ScopeFinding(
                id=f"scope-{len(findings) + 1}",
                category="Milestone",
                title=milestone.name or "Project milestone",
                description=milestone.description or milestone.name or "Project milestone",
                related_milestone=milestone.name,
                evidence=milestone.evidence,
            ))
        for sentence in sentences:
            normalized = sentence.strip()
            lower = normalized.lower()
            reference = _build_reference(document, normalized, chunks)
            if not lower:
                continue
            if any(keyword in lower for keyword in ["goal", "objective", "purpose", "aim", "mission", "target outcome"]):
                if not any(normalized in item for item in goals):
                    goals.append(normalized)
                    findings.append(ScopeFinding(
                        id=f"scope-{len(findings) + 1}",
                        category="Goal",
                        title="Project goal",
                        description=normalized,
                        evidence=[reference] if reference.excerpt else [],
                    ))
            if any(keyword in lower for keyword in ["objective", "deliverable", "outcome", "artifact", "report", "prototype", "feature", "module"]):
                if "deliverable" in lower or "outcome" in lower or "artifact" in lower:
                    if normalized not in deliverables:
                        deliverables.append(normalized)
                        findings.append(ScopeFinding(
                            id=f"scope-{len(findings) + 1}",
                            category="Deliverable",
                            title="Project deliverable",
                            description=normalized,
                            related_deliverable=normalized,
                            evidence=[reference] if reference.excerpt else [],
                        ))
                elif "objective" in lower or "goal" in lower:
                    if normalized not in objectives:
                        objectives.append(normalized)
                        findings.append(ScopeFinding(
                            id=f"scope-{len(findings) + 1}",
                            category="Objective",
                            title="Project objective",
                            description=normalized,
                            evidence=[reference] if reference.excerpt else [],
                        ))
            is_due_date_field = bool(re.fullmatch(r"due date\s*:\s*.+", lower))
            if not is_due_date_field and any(keyword in lower for keyword in ["milestone", "deadline", "target date", "due date", "launch date", "release date", "phase"]):
                milestone = ScopeMilestone(
                    name=normalized,
                    date=(_find_dates(normalized)[:1] or [None])[0],
                    status="identified",
                    description=normalized,
                    source=document.get("filename"),
                    evidence=[reference],
                )
                milestones.append(milestone)
                findings.append(ScopeFinding(
                    id=f"scope-{len(findings) + 1}",
                    category="Milestone",
                    title=milestone.name or "Project milestone",
                    description=normalized,
                    related_milestone=milestone.name,
                    evidence=[reference] if reference.excerpt else [],
                ))
            if any(keyword in lower for keyword in ["owner", "responsible", "lead", "assigned to", "team member"]):
                owner_match = re.search(r"(?:assigned to|owner|responsible|lead)\s+(?:is\s+)?([A-Za-z][A-Za-z .'-]+)", normalized, flags=re.IGNORECASE)
                task_match = re.search(r"(?:task|deliverable|component|module|workstream)[^:]*[:\-]?\s*([^.;]+)", normalized, flags=re.IGNORECASE)
                responsibilities.append(
                    ScopeResponsibility(
                        task=(task_match.group(1).strip() if task_match else normalized[:120]),
                        owner=(owner_match.group(1).strip() if owner_match else None),
                        role="project responsibility",
                        status="identified",
                        source=document.get("filename"),
                        evidence=[reference],
                    )
                )
                findings.append(ScopeFinding(
                    id=f"scope-{len(findings) + 1}",
                    category="Responsibility",
                    title="Project responsibility",
                    description=normalized,
                    related_task=(task_match.group(1).strip() if task_match else None),
                    evidence=[reference] if reference.excerpt else [],
                ))
            if any(keyword in lower for keyword in ["depends on", "dependency", "blocked by", "requires", "requires approval", "waiting on"]):
                dependency_description = normalized
                maybe_from = re.search(r"([A-Za-z0-9][A-Za-z0-9 /_-]+)\s+(?:depends on|requires|is blocked by)\s+([A-Za-z0-9][A-Za-z0-9 /_-]+)", normalized, flags=re.IGNORECASE)
                dependencies.append(
                    ScopeDependency(
                        from_task=(maybe_from.group(1).strip() if maybe_from else None),
                        to_task=(maybe_from.group(2).strip() if maybe_from else None),
                        description=dependency_description,
                        source=document.get("filename"),
                        evidence=[reference],
                    )
                )
                findings.append(ScopeFinding(
                    id=f"scope-{len(findings) + 1}",
                    category="Dependency",
                    title="Task dependency",
                    description=dependency_description,
                    related_task=(maybe_from.group(1).strip() if maybe_from else None),
                    evidence=[reference] if reference.excerpt else [],
                ))
            if any(keyword in lower for keyword in ["progress", "status", "on track", "behind schedule", "complete", "in progress", "delayed", "completed"]):
                progress.append(normalized)
                findings.append(ScopeFinding(
                    id=f"scope-{len(findings) + 1}",
                    category="Progress",
                    title="Project progress",
                    description=normalized,
                    evidence=[reference] if reference.excerpt else [],
                ))

    if not goals: missing_information.append("Project goals were not explicitly stated in the indexed documents.")
    if not deliverables: missing_information.append("No explicit deliverables or outcomes were identified.")
    if not milestones:
        for doc in documents:
            for date in _find_dates(doc.get("extracted_text") or ""):
                date_chunks = _load_chunks_for_document(doc["id"])
                reference = _build_reference(doc, date, date_chunks)
                milestones.append(ScopeMilestone(name=date, date=date, status="identified", description="Timeline reference found", source=doc.get("filename"), evidence=[reference] if reference.excerpt else []))
                findings.append(ScopeFinding(
                    id=f"scope-{len(findings) + 1}",
                    category="Milestone",
                    title=date,
                    description="Timeline reference found",
                    related_milestone=date,
                    evidence=[reference] if reference.excerpt else [],
                ))
    if not responsibilities:
        missing_information.append("No explicit responsibilities were identified in the documents.")
    if not dependencies:
        missing_information.append("No explicit task dependencies were identified in the sourced documents.")
    if not progress:
        missing_information.append("No progress or status statements were found for the project timeline.")

    if query:
        notes.append(f"Context query: {query}")
    elif documents and len(documents) > 1:
        notes.append("Analysis spans multiple documents to aggregate project scope and dependencies.")

    payload = ScopeAnalysis(
        goals=goals[:25],
        objectives=objectives[:25],
        deliverables=deliverables[:25],
        milestones=milestones[:25],
        responsibilities=responsibilities[:25],
        dependencies=dependencies[:25],
        progress=progress[:25],
        findings=findings[:100],
        missing_information=missing_information,
        source_documents=sorted(set(source_documents)),
        notes=notes,
    ).model_dump(mode="json")
    if persist:
        _persist_analysis("scope", [document["id"] for document in documents], payload)
    return payload


def analyze_risks(
    document_id: str | None = None,
    document_ids: list[str] | None = None,
    query: str | None = None,
    top_k: int = 5,
    persist: bool = True,
) -> dict[str, Any]:
    documents = _load_documents(document_id=document_id, document_ids=document_ids)
    if not documents:
        payload = RiskAnalysis(
            findings=[],
            missing_information=["No indexed documents are available for risk analysis."],
            source_documents=[],
            rules_used=["Date and status comparisons", "Dependency and blocker keyword detection"],
        ).model_dump(mode="json")
        if persist:
            _persist_analysis("risk", [], payload)
        return payload

    findings: list[RiskFinding] = []
    missing_information: list[str] = []
    source_documents: list[str] = []
    rules_used = [
        "Treat delayed, overdue, or blocked phrases as risk evidence when tied to a project milestone or dependency.",
        "Classify risk levels by severity keywords and the presence of missing approvals or schedule slippage.",
        "Only forecast delivery when schedule dates and progress data appear together.",
    ]

    for document in documents:
        source_documents.append(document.get("filename") or document.get("id") or "unknown")
        text = document.get("extracted_text") or ""
        sentences = _sentence_split(text)
        chunks = _load_chunks_for_document(document["id"])
        for sentence in sentences:
            normalized = sentence.strip()
            lower = normalized.lower()
            if not any(keyword in lower for keyword in ["risk", "delay", "delayed", "overdue", "blocked", "dependency", "approval", "schedule", "issue", "behind"]):
                continue
            reference = _build_reference(document, sentence, chunks)
            title = "Project risk identified"
            if "delay" in lower or "delayed" in lower:
                title = "Schedule delay"
            elif "overdue" in lower:
                title = "Overdue milestone"
            elif "blocked" in lower or "dependency" in lower:
                title = "Dependency or blocker risk"
            elif "approval" in lower:
                title = "Approval dependency risk"

            level = "Low"
            if any(keyword in lower for keyword in ["critical", "cancelled", "cannot proceed", "no approval", "hard blocker"]):
                level = "Critical"
            elif any(keyword in lower for keyword in ["blocked", "overdue", "must be resolved", "resource shortage", "dependency gap", "risk"]):
                level = "High"
            elif any(keyword in lower for keyword in ["delay", "delayed", "risk", "approval pending", "schedule"]):
                level = "Medium"

            recommendations = []
            if "approval" in lower:
                recommendations.append("Escalate the pending decision and document the approval owner and deadline.")
            if "dependency" in lower or "blocked" in lower:
                recommendations.append("Map the missing dependency and assign a clear owner for resolution.")
            if "delay" in lower or "overdue" in lower:
                recommendations.append("Review the milestone dates, adjust the plan if necessary, and communicate a recovery path.")
            if not recommendations:
                recommendations.append("Keep a mitigation owner and track the risk until it is resolved or disproven.")

            forecast = None
            if re.search(r"\b(?:due|deadline|target|by)\b", lower) or any(date_token in lower for date_token in ["202", "2024", "2025", "2026", "Q1", "Q2", "Q3", "Q4"]):
                if any(keyword in lower for keyword in ["on track", "completed", "progress", "status"]):
                    forecast = "A forecast is possible only when there is date and progress evidence together; the current text suggests the schedule should be reviewed with the project owner."
                else:
                    forecast = "Forecast unavailable because the documents do not include enough schedule and progress evidence to estimate completion reliably."

            findings.append(
                RiskFinding(
                    id=f"risk-{len(findings) + 1}",
                    title=title,
                    level=level,
                    description=normalized,
                    affected_milestones=[segment for segment in _find_dates(normalized) if segment],
                    evidence=[reference] if reference.excerpt else [],
                    recommendations=recommendations,
                    forecast=forecast,
                    inference=False,
                )
            )

    if not findings:
        missing_information.append("No risk evidence was found in the indexed project documents.")
    if not source_documents:
        missing_information.append("No source documents were associated with the analysis.")

    payload = RiskAnalysis(
        findings=findings[:25],
        missing_information=missing_information,
        source_documents=sorted(set(source_documents)),
        rules_used=rules_used,
    ).model_dump(mode="json")
    if persist:
        _persist_analysis("risk", [document["id"] for document in documents], payload)
    return payload


def analyze_blockers(
    document_id: str | None = None,
    document_ids: list[str] | None = None,
    query: str | None = None,
    top_k: int = 5,
    persist: bool = True,
) -> dict[str, Any]:
    documents = _load_documents(document_id=document_id, document_ids=document_ids)
    if not documents:
        payload = BlockerAnalysis(
            blockers=[],
            pending_decisions=[],
            action_items=[],
            missing_information=["No indexed documents are available for blocker analysis."],
            source_documents=[],
            notes=["Add documents before running this analysis."],
        ).model_dump(mode="json")
        if persist:
            _persist_analysis("blockers", [], payload)
        return payload

    blockers: list[BlockerItem] = []
    pending_decisions: list[BlockerItem] = []
    action_items: list[BlockerItem] = []
    missing_information: list[str] = []
    source_documents: list[str] = []
    notes: list[str] = []

    for document in documents:
        source_documents.append(document.get("filename") or document.get("id") or "unknown")
        text = document.get("extracted_text") or ""
        sentences = _sentence_split(text)
        chunks = _load_chunks_for_document(document["id"])
        for sentence in sentences:
            normalized = sentence.strip()
            lower = normalized.lower()
            is_pending_decision = any(keyword in lower for keyword in [
                "pending decision", "decision required", "approval required", "needs approval",
                "needs to approve", "need to approve", "must approve", "should approve",
                "must be approved", "approval needed", "requires approval",
                "must decide", "need to decide", "needs to decide", "should decide",
                "decide which", "decide whether", "decide what", "decide who", "decide when",
                "needs a decision", "decision is pending",
            ])
            is_action_item = any(keyword in lower for keyword in ["action item", "follow up", "next step"]) or bool(
                re.search(
                    r"\b(?:must|will|should|needs? to|has to|have to)\s+"
                    r"(?:complete|finish|deliver|implement|update|review|validate|submit|prepare|send|resolve|"
                    r"test|create|provide|investigate|schedule|confirm|document|coordinate|finalize|approve)\b",
                    normalized,
                    flags=re.IGNORECASE,
                )
                )
            is_blocker = any(keyword in lower for keyword in [
                "blocker", "blocked", "issue", "waiting on", "waiting for", "awaiting",
                "delayed because", "is delayed", "are delayed", "behind schedule",
                "approval required", "pending approval",
            ])
            if not (is_blocker or is_pending_decision or is_action_item):
                continue
            reference = _build_reference(document, sentence, chunks)
            category = "issue"
            status = "open"
            priority = None
            if any(keyword in lower for keyword in ["critical", "urgent", "high priority"]):
                priority = "High"
            elif any(keyword in lower for keyword in ["medium", "moderate"]):
                priority = "Medium"
            elif any(keyword in lower for keyword in ["low priority", "minor"]):
                priority = "Low"

            due_date = None
            for match in _find_dates(normalized):
                due_date = match
                break

            assignee = None
            assignee_match = re.search(r"(?:assigned to|owner|lead|responsible|follow up with)\s+([A-Z][A-Za-z .'-]+)", normalized)
            if not assignee_match:
                assignee_match = re.search(
                    r"^\s*(?:\d+[.)]\s*)?action\s+item\s*[:\-]\s*([A-Z][A-Za-z'-]*)\s+(?:must|will|should)\b",
                    normalized,
                    flags=re.IGNORECASE,
                )
            if not assignee_match and is_action_item:
                assignee_match = re.search(
                    r"^\s*(?:\d+[.)]\s*)?([A-Z][a-z][A-Za-z'-]*)\s+"
                    r"(?:must|will|should|needs? to|has to|have to)\b",
                    normalized,
                )
            if not assignee_match and is_action_item:
                assignee_match = re.search(
                    r"^\s*(?:\d+[.)]\s*)?(?:action\s+item\s*[:\-]\s*)?"
                    r"(?:the\s+)?([A-Za-z][A-Za-z'-]*(?:\s+[A-Za-z][A-Za-z'-]*){0,2})\s+"
                    r"(?:must|will|should|needs? to|has to|have to)\b",
                    normalized,
                    flags=re.IGNORECASE,
                )
            if assignee_match:
                assignee = assignee_match.group(1).strip().rstrip(".,;")

            related_task = None
            task_match = re.search(r"(?:task|deliverable|milestone|workstream|component)\s*[:\-]?\s*([A-Za-z0-9][A-Za-z0-9 /._-]+)", normalized, flags=re.IGNORECASE)
            if task_match:
                related_task = task_match.group(1).strip()

            recommendation = None
            if "approval" in lower or "decision" in lower:
                recommendation = "Escalate the decision and record the approver, decision date, and rationale."
            elif "blocked" in lower or "blocker" in lower:
                recommendation = "Document the blocker, assign an accountable owner, and track the dependency to closure."
            elif "action item" in lower:
                recommendation = "Convert the action item into a tracked task with an owner and due date."

            payload_item = BlockerItem(
                id=f"issue-{len(blockers) + len(pending_decisions) + len(action_items) + 1}",
                description=normalized,
                category=category,
                status=status,
                assignee=assignee,
                due_date=due_date,
                priority=priority,
                related_task=related_task,
                source_document=document.get("filename"),
                evidence=[reference] if reference.excerpt else [],
                recommendation=recommendation,
            )

            if is_pending_decision:
                pending_decisions.append(payload_item)
            elif is_action_item:
                action_items.append(payload_item)
            else:
                blockers.append(payload_item)

    if not blockers and not pending_decisions and not action_items:
        missing_information.append("No blocker, pending decision, or action item evidence was found in the indexed documents.")
    if not source_documents:
        missing_information.append("No source documents were provided for blocker analysis.")

    payload = BlockerAnalysis(
        blockers=blockers[:25],
        pending_decisions=pending_decisions[:25],
        action_items=action_items[:25],
        missing_information=missing_information,
        source_documents=sorted(set(source_documents)),
        notes=notes,
    ).model_dump(mode="json")
    if persist:
        _persist_analysis("blockers", [document["id"] for document in documents], payload)
    return payload


def resolve_analysis_request(body: AnalysisRequest | None = None, document_id: str | None = None, document_ids: list[str] | None = None, query: str | None = None, top_k: int = 5) -> dict[str, Any]:
    request = body or AnalysisRequest(document_id=document_id, document_ids=document_ids, query=query, top_k=top_k)
    selected = request.document_ids if request.document_ids else []
    if request.document_id:
        selected = [*dict.fromkeys([request.document_id, *selected])]
    return {
        "document_id": request.document_id,
        "document_ids": selected,
        "query": request.query,
        "top_k": request.top_k,
    }


class LLMConfigurationError(RuntimeError):
    pass


class LLMGenerationError(RuntimeError):
    pass


class LLMTimeoutError(LLMGenerationError):
    pass


@lru_cache(maxsize=3)
def _openai_client(api_key: str, timeout_seconds: float) -> Any:
    import openai

    return openai.OpenAI(api_key=api_key, timeout=timeout_seconds, max_retries=1)


@lru_cache(maxsize=3)
def _azure_openai_client(api_key: str, endpoint: str, timeout_seconds: float) -> Any:
    from openai import AzureOpenAI

    return AzureOpenAI(
        api_key=api_key,
        api_version="2024-02-01",
        azure_endpoint=endpoint,
        timeout=timeout_seconds,
        max_retries=1,
    )


@lru_cache(maxsize=3)
def _ollama_http_client(base_url: str, timeout_seconds: float) -> httpx.Client:
    return httpx.Client(
        base_url=base_url,
        timeout=httpx.Timeout(timeout_seconds),
        limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
    )


def _is_timeout_error(error: Exception) -> bool:
    current: BaseException | None = error
    while current is not None:
        if isinstance(current, (httpx.TimeoutException, TimeoutError)):
            return True
        if type(current).__name__ in {"APITimeoutError", "ReadTimeout", "ConnectTimeout"}:
            return True
        current = current.__cause__
    return False


def maybe_call_llm(prompt: str, context: str) -> str:
    provider = settings.ai_provider
    if provider == "rule_based":
        raise LLMConfigurationError(
            "The assistant LLM is disabled. Set AI_PROVIDER=openai and configure OPENAI_API_KEY, "
            "or configure Ollama or Azure OpenAI in the backend environment."
        )
    if provider not in {"openai", "azure", "ollama"}:
        raise LLMConfigurationError(
            f"Unsupported AI_PROVIDER '{provider}'. Choose 'ollama', 'openai', or 'azure'."
        )
    if provider == "openai" and not settings.openai_api_key_configured:
        raise LLMConfigurationError(
            "AI_PROVIDER=openai but OPENAI_API_KEY is not configured. Set it in the repository-root "
            ".env file or the backend process environment, then restart FastAPI."
        )
    if provider == "azure" and not settings.azure_openai_api_key_configured:
        raise LLMConfigurationError(
            "AI_PROVIDER=azure but AZURE_OPENAI_API_KEY is not configured. Set it in the "
            "repository-root .env file or the backend process environment, then restart FastAPI."
        )
    if provider == "azure" and not settings.azure_openai_endpoint:
        raise LLMConfigurationError(
            "AI_PROVIDER=azure but AZURE_OPENAI_ENDPOINT is not configured."
        )
    if provider == "azure" and not settings.azure_openai_deployment:
        raise LLMConfigurationError(
            "AI_PROVIDER=azure but AZURE_OPENAI_DEPLOYMENT is not configured."
        )
    if provider == "ollama":
        if not settings.ollama_model:
            raise LLMConfigurationError(
                "AI_PROVIDER=ollama but OLLAMA_MODEL is not configured. Set it to a model already installed in Ollama."
            )
        ollama_url = urlparse(settings.ollama_base_url)
        if (
            ollama_url.scheme not in {"http", "https"}
            or not ollama_url.hostname
            or ollama_url.username
            or ollama_url.password
        ):
            raise LLMConfigurationError(
                "OLLAMA_BASE_URL must be an HTTP or HTTPS URL."
            )

    try:
        timeout_seconds = settings.llm_request_timeout_seconds
    except ValueError as error:
        raise LLMConfigurationError(str(error)) from error

    started = perf_counter()
    try:
        if provider == "openai":
            client = _openai_client(settings.openai_api_key, timeout_seconds)
            response = client.responses.create(
                model=settings.openai_model,
                instructions=prompt,
                input=f"Retrieved project evidence:\n{context[:24000]}",
                max_output_tokens=384,
            )
            if (
                getattr(response, "status", None) == "incomplete"
                or getattr(response, "incomplete_details", None) is not None
            ):
                raise LLMGenerationError(
                    "The configured OpenAI service returned an incomplete answer. Please retry the question."
                )
            answer = getattr(response, "output_text", None)
        elif provider == "azure":
            client = _azure_openai_client(
                settings.azure_openai_api_key,
                settings.azure_openai_endpoint,
                timeout_seconds,
            )
            response = client.responses.create(
                model=settings.azure_openai_deployment,
                instructions=prompt,
                input=f"Retrieved project evidence:\n{context[:24000]}",
                max_output_tokens=384,
            )
            if (
                getattr(response, "status", None) == "incomplete"
                or getattr(response, "incomplete_details", None) is not None
            ):
                raise LLMGenerationError(
                    "The configured Azure OpenAI service returned an incomplete answer. Please retry the question."
                )
            answer = getattr(response, "output_text", None)
        else:
            response = _ollama_http_client(
                settings.ollama_base_url,
                timeout_seconds,
            ).post(
                "/api/chat",
                json={
                    "model": settings.ollama_model,
                    "stream": False,
                    "keep_alive": "10m",
                    "options": {
                        "temperature": 0,
                        "top_p": 0.65,
                        "num_ctx": 4096,
                        "num_predict": 192,
                    },
                    "messages": [
                        {"role": "system", "content": prompt},
                        {
                            "role": "user",
                            "content": f"Retrieved project evidence:\n{context[:24000]}",
                        },
                    ],
                },
            )
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError("Ollama returned an invalid response.")
            if result.get("done_reason") == "length":
                raise LLMGenerationError(
                    "The configured Ollama service returned an incomplete answer. Narrow the question and retry."
                )
            message = result.get("message")
            answer = message.get("content") if isinstance(message, dict) else None
        if not isinstance(answer, str) or not answer.strip():
            raise LLMGenerationError("The configured LLM returned an empty answer. Please retry.")
        provider_duration_ms = (
            result.get("total_duration", 0) / 1_000_000
            if provider == "ollama" and isinstance(result.get("total_duration"), (int, float))
            else None
        )
        prompt_eval_ms = (
            result.get("prompt_eval_duration", 0) / 1_000_000
            if provider == "ollama" and isinstance(result.get("prompt_eval_duration"), (int, float))
            else None
        )
        eval_ms = (
            result.get("eval_duration", 0) / 1_000_000
            if provider == "ollama" and isinstance(result.get("eval_duration"), (int, float))
            else None
        )
        logger.info(
            "LLM generation completed provider=%s model=%s duration_ms=%.1f "
            "provider_total_ms=%s prompt_eval_ms=%s eval_ms=%s prompt_eval_tokens=%s "
            "eval_tokens=%s done_reason=%s prompt_chars=%d context_chars=%d output_chars=%d",
            provider,
            settings.openai_model if provider == "openai" else (
                settings.azure_openai_deployment if provider == "azure" else settings.ollama_model
            ),
            (perf_counter() - started) * 1000,
            f"{provider_duration_ms:.1f}" if provider_duration_ms is not None else "unavailable",
            f"{prompt_eval_ms:.1f}" if prompt_eval_ms is not None else "unavailable",
            f"{eval_ms:.1f}" if eval_ms is not None else "unavailable",
            result.get("prompt_eval_count", "unavailable") if provider == "ollama" else "unavailable",
            result.get("eval_count", "unavailable") if provider == "ollama" else "unavailable",
            result.get("done_reason", "unavailable") if provider == "ollama" else "unavailable",
            len(prompt),
            len(context[:24000]),
            len(answer),
        )
        return answer.strip()
    except (LLMConfigurationError, LLMGenerationError):
        raise
    except ModuleNotFoundError as error:
        logger.exception("Configured LLM provider SDK is unavailable")
        raise LLMConfigurationError(
            "The OpenAI SDK is not installed. Install backend requirements and restart the API."
        ) from error
    except Exception as error:
        if _is_timeout_error(error):
            elapsed_seconds = perf_counter() - started
            logger.warning(
                "LLM generation timed out provider=%s duration_seconds=%.1f timeout_seconds=%.1f",
                provider,
                elapsed_seconds,
                timeout_seconds,
            )
            raise LLMTimeoutError(
                f"The configured {provider} answer-generation service timed out after "
                f"{timeout_seconds:g} seconds. Retry the question."
            ) from error
        logger.exception("Configured %s answer generation failed", provider)
        raise LLMGenerationError(
            f"The configured {provider} answer-generation service could not complete the request. "
            "Check provider access, model/deployment name, quota, and API logs, then retry."
        ) from error
