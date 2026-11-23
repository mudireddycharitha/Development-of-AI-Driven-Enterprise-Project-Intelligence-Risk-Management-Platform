from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


def classify_risk_severity(impact: str | None, likelihood: str | None) -> str | None:
    """Classify severity only when both source-backed impact and likelihood exist."""
    impact_key = (impact or "").strip().casefold()
    likelihood_key = (likelihood or "").strip().casefold()
    matrix = {
        "low": {"low": "Low", "medium": "Low", "high": "Medium", "critical": "Medium"},
        "medium": {"low": "Low", "medium": "Medium", "high": "High", "critical": "High"},
        "high": {"low": "Medium", "medium": "High", "high": "Critical", "critical": "Critical"},
        "critical": {"low": "High", "medium": "Critical", "high": "Critical", "critical": "Critical"},
    }
    return matrix.get(impact_key, {}).get(likelihood_key)


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
    scope: list[str] = Field(default_factory=list)
    deliverables: list[str] = Field(default_factory=list)
    milestones: list[ScopeMilestone] = Field(default_factory=list)
    project_start_date: str | None = None
    project_end_date: str | None = None
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
    level: Literal["Low", "Medium", "High", "Critical"] | None = None
    description: str
    category: str | None = None
    impact: str | None = None
    likelihood: str | None = None
    owner: str | None = None
    affected_milestones: list[str] = Field(default_factory=list)
    evidence: list[EvidenceReference] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    forecast: str | None = None
    inference: bool = False

    @model_validator(mode="after")
    def classify_supported_severity(self) -> "RiskFinding":
        if self.level is None:
            self.level = classify_risk_severity(self.impact, self.likelihood)
        return self


class RiskAnalysis(BaseModel):
    findings: list[RiskFinding] = Field(default_factory=list)
    severity_counts: dict[str, int] = Field(default_factory=dict)
    missing_information: list[str] = Field(default_factory=list)
    source_documents: list[str] = Field(default_factory=list)
    rules_used: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def count_findings_by_severity(self) -> "RiskAnalysis":
        levels = ("Critical", "High", "Medium", "Low", "Unrated")
        self.severity_counts = {
            level: sum(1 for finding in self.findings if (finding.level or "Unrated") == level)
            for level in levels
        }
        return self


class BlockerItem(BaseModel):
    id: str
    description: str
    category: str = "issue"
    status: str | None = None
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
    unresolved_issues: list[BlockerItem] = Field(default_factory=list)
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
