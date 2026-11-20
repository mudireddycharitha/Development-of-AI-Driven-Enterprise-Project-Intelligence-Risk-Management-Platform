from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.analysis import EvidenceReference


class DocumentSelectionRequest(BaseModel):
    document_ids: list[str] | None = Field(default=None, max_length=100)


class AssistantRequest(BaseModel):
    session_id: UUID
    question: str = Field(min_length=1, max_length=2000)
    document_ids: list[str] | None = Field(default=None, max_length=100)
    top_k: int = Field(default=6, ge=1, le=12)


class AssistantSource(BaseModel):
    document_id: str
    filename: str
    chunk_id: str
    excerpt: str
    location: dict[str, Any] = Field(default_factory=dict)
    distance: float | None = None


class AssistantResponse(BaseModel):
    session_id: UUID
    question: str
    answer: str
    answer_mode: Literal["llm", "health_analysis", "extractive", "insufficient_evidence"]
    sources: list[AssistantSource] = Field(default_factory=list)
    health: dict[str, Any] | None = None
    created_at: str


class AssistantHistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    sources: list[AssistantSource] = Field(default_factory=list)
    answer_mode: Literal["llm", "health_analysis", "extractive", "insufficient_evidence"] | None = None
    health: dict[str, Any] | None = None
    created_at: str


class AssistantConversation(BaseModel):
    session_id: UUID
    messages: list[AssistantHistoryMessage] = Field(default_factory=list)


class ProjectHealthDimension(BaseModel):
    name: str
    score: float | None
    available: bool
    explanation: str
    factors: list[str] = Field(default_factory=list)


class ProjectHealthResult(BaseModel):
    id: str
    calculated_at: str
    overall_score: float | None
    classification: Literal["Healthy", "Needs Attention", "At Risk", "Insufficient Data"]
    dimensions: dict[str, ProjectHealthDimension]
    weights: dict[str, float]
    identified_risks: list[dict[str, Any]] = Field(default_factory=list)
    unresolved_blockers: list[dict[str, Any]] = Field(default_factory=list)
    pending_decisions: list[dict[str, Any]] = Field(default_factory=list)
    delayed_milestones: list[dict[str, Any]] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)
    document_count: int


class DocumentationItem(BaseModel):
    id: str
    title: str
    description: str
    source_document: str
    evidence: EvidenceReference


class DocumentationResult(BaseModel):
    id: str
    generated_at: str
    document_ids: list[str]
    source_documents: list[str]
    user_stories: list[dict[str, Any]]
    risk_register: list[dict[str, Any]]
    action_items: list[dict[str, Any]]
    pending_decisions: list[dict[str, Any]]
    total_generated_items: int
