import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


_RISK_LEVELS = {"low": (1, "Low"), "medium": (2, "Medium"), "high": (3, "High"), "critical": (4, "Critical")}


def normalize_risk_level(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = (value or "").strip().casefold()
    if normalized in _RISK_LEVELS:
        return _RISK_LEVELS[normalized][1]
    match = re.fullmatch(r"(low|medium|high|critical)(?:\s+risk)?", normalized)
    return _RISK_LEVELS[match.group(1)][1] if match else None


def _assess_risk_dimension(
    value: str | None,
    dimension: str,
    evidence: str = "",
) -> tuple[int | None, str | None, str | None]:
    text = " ".join(part for part in (value or "", evidence) if part).casefold()
    direct = (value or "").strip().casefold()
    explicit_dimension = (
        r"\bimpact\s*(?:is|:|=)?\s*(critical|high|medium|low)\b"
        r"|\b(critical|high|medium|low)\s+impact\b"
        if dimension == "impact"
        else r"\b(?:probability|likelihood)\s*(?:is|:|=)?\s*"
        r"(very likely|very unlikely|moderate chance|not expected|critical|high|medium|low|likely|possible|plausible|rare|unlikely)\b"
    )
    source_match = re.search(explicit_dimension, evidence.casefold())
    if source_match:
        source_value = next(group for group in source_match.groups() if group)
        if dimension == "impact" or source_value in _RISK_LEVELS:
            score, label = _RISK_LEVELS[source_value]
            return score, label, f"source states {dimension} as {label}"
        if source_value in {"rare", "very unlikely", "unlikely", "not expected"}:
            score, label = _RISK_LEVELS["low"]
            return score, label, f"source states {dimension} as {label}"
        score, label = _RISK_LEVELS["high" if source_value in {"likely", "very likely"} else "medium"]
        return score, label, f"source states {dimension} as {label}"

    if direct in _RISK_LEVELS:
        score, label = _RISK_LEVELS[direct]
        return score, label, f"source states {dimension} as {label}"
    qualified_level = re.fullmatch(
        r"(?:impact|probability|likelihood)?\s*(?::|is)?\s*(low|medium|high|critical)"
        r"(?:\s+(?:impact|probability|likelihood))?",
        direct,
    )
    if qualified_level:
        score, label = _RISK_LEVELS[qualified_level.group(1)]
        return score, label, f"source states {dimension} as {label}"

    synonyms = (
        (r"\b(?:rare|very unlikely|unlikely|not expected)\b", "low"),
        (r"\b(?:possible|plausible|moderate chance|may|might)\b", "medium"),
        (r"\b(?:likely|probable|almost certain|very likely)\b", "high"),
    ) if dimension == "probability" else ()
    for pattern, level in synonyms:
        if re.search(pattern, direct):
            score, label = _RISK_LEVELS[level]
            return score, label, f"{dimension} inferred from source wording ({label.casefold()} likelihood)"

    if dimension == "impact":
        if re.search(r"\b(?:critical|project failure|essential objectives?|threatens? delivery|cancellation)\b", text):
            score, label = _RISK_LEVELS["critical"]
            return score, label, "critical impact inferred from the stated threat to delivery/objectives"
        if re.search(r"\b(?:blocks?|blocked|prevents?|cannot proceed|cannot begin|stops?|significant|major)\b", text):
            score, label = _RISK_LEVELS["high"]
            return score, label, "high impact inferred from the source stating work or validation is blocked"
        if re.search(r"\b(?:determines whether .*pilot|pilot includes?|limited impact|feature unavailable|affects? the pilot)\b", text):
            score, label = _RISK_LEVELS["medium"]
            return score, label, "medium impact inferred from the documented effect on pilot functionality"
        if re.search(r"\b(?:delay(?:ed)?|overdue|schedule impact|delivery impact|deadline)\b", text):
            score, label = _RISK_LEVELS["medium"]
            return score, label, "medium impact inferred from documented schedule or delivery delay"
        if re.search(r"\b(?:minor|limited|routine monitoring)\b", text):
            score, label = _RISK_LEVELS["low"]
            return score, label, "low impact inferred from the source describing a limited effect"
    elif re.search(
        r"\b(?:blocked|delayed|overdue|not confirmed|pending|waiting(?: for| on)?|awaiting|missing approval)\b",
        text,
    ):
        score, label = _RISK_LEVELS["high"]
        return score, label, "high probability inferred from an explicitly pending, blocked, or unconfirmed status"
    elif re.search(r"\b(?:may|might|possible|could)\b", text):
        score, label = _RISK_LEVELS["medium"]
        return score, label, "medium probability inferred from the source describing a possible event"
    return None, None, None


def _severity_for_score(score: int) -> str:
    """Risk score is probability (1-4) x impact (1-4): 1-2 Low, 3-5 Medium, 6-9 High, 10-16 Critical."""
    if score <= 2:
        return "Low"
    if score <= 5:
        return "Medium"
    if score <= 9:
        return "High"
    return "Critical"


def classify_risk_severity(impact: str | None, likelihood: str | None) -> str | None:
    probability_score, _, _ = _assess_risk_dimension(likelihood, "probability")
    impact_score, _, _ = _assess_risk_dimension(impact, "impact")
    if probability_score is None or impact_score is None:
        return None
    return _severity_for_score(probability_score * impact_score)


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
    owner: str | None = None
    source: str | None = None
    evidence: list[EvidenceReference] = Field(default_factory=list)


class MainProjectGoal(BaseModel):
    title: str = "Main project goal"
    statement: str
    problem: str | None = None
    expected_outcome: str | None = None
    evidence: list[EvidenceReference] = Field(default_factory=list)
    problem_evidence: list[EvidenceReference] = Field(default_factory=list)
    outcome_evidence: list[EvidenceReference] = Field(default_factory=list)


class SupportingObjective(BaseModel):
    id: str
    title: str
    description: str
    contribution: str
    related_deliverable: str | None = None
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
    main_project_goal: MainProjectGoal | None = None
    supporting_objectives: list[SupportingObjective] = Field(default_factory=list)
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
    probability_score: int | None = None
    impact_score: int | None = None
    severity_score: int | None = None
    severity_reason: str | None = None
    insufficient_evidence: list[str] = Field(default_factory=list)
    recommended_mitigation: str | None = None
    owner: str | None = None
    affected_milestones: list[str] = Field(default_factory=list)
    related_project_goal: str | None = None
    affected_objectives: list[str] = Field(default_factory=list)
    evidence: list[EvidenceReference] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    forecast: str | None = None
    inference: bool = False

    @field_validator("level", mode="before")
    @classmethod
    def normalize_level(cls, value: Any) -> str | None:
        return normalize_risk_level(value if isinstance(value, str) else None)

    @model_validator(mode="after")
    def classify_supported_severity(self) -> "RiskFinding":
        explicit_level = next(
            (label for key, (_, label) in _RISK_LEVELS.items() if key == (self.level or "").strip().casefold()),
            None,
        )
        impact_score, impact_level, impact_reason = _assess_risk_dimension(
            self.impact, "impact", self.description,
        )
        probability_score, probability_level, probability_reason = _assess_risk_dimension(
            self.likelihood, "probability", self.description,
        )
        self.impact_score = impact_score
        self.probability_score = probability_score
        if impact_level:
            self.impact = impact_level
        if probability_level:
            self.likelihood = probability_level
        missing = []
        if self.impact_score is None:
            missing.append("impact")
        if self.probability_score is None:
            missing.append("probability")
        self.insufficient_evidence = missing
        if self.impact_score is not None and self.probability_score is not None:
            self.severity_score = self.impact_score * self.probability_score
            self.level = _severity_for_score(self.severity_score)
            self.severity_reason = (
                f"Probability {self.likelihood} ({self.probability_score}) x impact "
                f"{self.impact} ({self.impact_score}) = {self.severity_score}; "
                f"classified as {self.level}. "
                f"{probability_reason or 'Probability was source-backed.'}; "
                f"{impact_reason or 'Impact was source-backed'}."
            )
        elif explicit_level:
            self.level = explicit_level
            self.severity_reason = (
                f"The source states {explicit_level} severity; probability and/or impact evidence "
                f"is insufficient for an independent score ({' and '.join(missing)} missing)."
            )
        else:
            self.level = None
            self.severity_reason = (
                "Unrated: sufficient source evidence was not available to determine "
                + " and ".join(missing)
                + "."
            )
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
    related_project_goal: str | None = None
    affected_objectives: list[str] = Field(default_factory=list)
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
