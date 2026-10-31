from typing import Any, Literal

from pydantic import BaseModel, Field


class EvidenceReference(BaseModel):
    document_id: str | None = None
    filename: str | None = None
    chunk_id: str | None = None
    excerpt: str = ""
    location: dict[str, Any] = Field(default_factory=dict)
    source: str | None = None


class ScopeMilestone(BaseModel):
    name: str | None = None
    date: str | None = None
    status: str | None = None
    description: str | None = None
    source: str | None = None
    evidence: list[EvidenceReference] = Field(default_factory=list)


class ScopeDependency(BaseModel):
    from_task: str | None = None
    to_task: str | None = None
    description: str | None = None
    source: str | None = None
    evidence: list[EvidenceReference] = Field(default_factory=list)


class ScopeResponsibility(BaseModel):
    task: str | None = None
    owner: str | None = None
    role: str | None = None
    status: str | None = None
    source: str | None = None
    evidence: list[EvidenceReference] = Field(default_factory=list)


class ScopeFinding(BaseModel):
    id: str
    category: str
    title: str
    description: str
    related_milestone: str | None = None
    related_task: str | None = None
    related_deliverable: str | None = None
    evidence: list[EvidenceReference] = Field(default_factory=list)


class ScopeAnalysis(BaseModel):
    goals: list[str] = Field(default_factory=list)
    objectives: list[str] = Field(default_factory=list)
    deliverables: list[str] = Field(default_factory=list)
    milestones: list[ScopeMilestone] = Field(default_factory=list)
    responsibilities: list[ScopeResponsibility] = Field(default_factory=list)
    dependencies: list[ScopeDependency] = Field(default_factory=list)
    progress: list[str] = Field(default_factory=list)
    findings: list[ScopeFinding] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    source_documents: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class RiskFinding(BaseModel):
    id: str
    title: str
    level: Literal["Low", "Medium", "High", "Critical"]
    description: str
    affected_milestones: list[str] = Field(default_factory=list)
    evidence: list[EvidenceReference] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    forecast: str | None = None
    inference: bool = False


class RiskAnalysis(BaseModel):
    findings: list[RiskFinding] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    source_documents: list[str] = Field(default_factory=list)
    rules_used: list[str] = Field(default_factory=list)


class BlockerItem(BaseModel):
    id: str
    description: str
    category: str = "issue"
    status: str = "open"
    assignee: str | None = None
    due_date: str | None = None
    priority: str | None = None
    related_task: str | None = None
    related_milestone: str | None = None
    source_document: str | None = None
    evidence: list[EvidenceReference] = Field(default_factory=list)
    recommendation: str | None = None


class BlockerAnalysis(BaseModel):
    blockers: list[BlockerItem] = Field(default_factory=list)
    pending_decisions: list[BlockerItem] = Field(default_factory=list)
    action_items: list[BlockerItem] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    source_documents: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class AnalysisRequest(BaseModel):
    document_id: str | None = None
    document_ids: list[str] | None = None
    top_k: int = Field(default=5, ge=1, le=25)
    query: str | None = None
