import logging
import re
from datetime import date, datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException

from app.database.db import connect
from app.services.documentation import get_latest_documentation
from app.services.project_agents import analyze_blockers, analyze_risks, analyze_scope
from app.services.project_health import calculate_project_health

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/project-insights", tags=["project insights"])

_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%B %d, %Y", "%b %d, %Y")


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    for date_format in _DATE_FORMATS:
        try:
            return datetime.strptime(value.strip(), date_format).date()
        except ValueError:
            continue
    return None


def _action_status(item: dict[str, Any], today: date) -> str:
    source_status = (item.get("status") or "").strip().casefold()
    if source_status in {"completed", "complete", "done", "closed", "resolved"}:
        return "completed"
    due_date = _parse_date(item.get("due_date"))
    if due_date and due_date < today:
        return "overdue"
    return "pending"


def _milestone_status(item: dict[str, Any], today: date) -> str:
    details = " ".join(
        str(item.get(key) or "")
        for key in ("name", "description", "status")
    )
    if re.search(r"\b(completed|complete|done|closed|delivered)\b", details, flags=re.IGNORECASE):
        return "completed"
    if re.search(r"\b(delayed|overdue|behind schedule)\b", details, flags=re.IGNORECASE):
        return "delayed"
    if re.search(r"\bblocked\b", details, flags=re.IGNORECASE):
        return "blocked"
    if re.search(r"\b(at risk)\b", details, flags=re.IGNORECASE):
        return "at_risk"
    if re.search(r"\b(in progress|ongoing)\b", details, flags=re.IGNORECASE):
        return "in_progress"
    if re.search(r"\b(pending|not started|open)\b", details, flags=re.IGNORECASE):
        return "pending"
    due_date = _parse_date(item.get("date"))
    incomplete = re.search(
        r"\b(in progress|pending|blocked|not started|at risk|open)\b",
        details,
        flags=re.IGNORECASE,
    )
    if due_date and due_date < today and incomplete:
        return "delayed"
    return "not_stated"


def _is_unresolved_blocker(item: dict[str, Any]) -> bool:
    description = item.get("description") or ""
    status = (item.get("status") or "").casefold()
    return (
        status not in {"resolved", "closed", "completed", "done"}
        and not re.search(r"\b(resolved|closed|completed|done)\b", description, flags=re.IGNORECASE)
        and not re.search(
            r"\b(?:no|zero)\s+(?:open\s+)?blockers?\b|\bno unresolved blockers?\b",
            description,
            flags=re.IGNORECASE,
        )
    )


def _risk_category(item: dict[str, Any]) -> str:
    text = f"{item.get('title', '')} {item.get('description', '')}".casefold()
    for pattern, category in (
        (r"schedule|timeline|delay|deadline|milestone", "Schedule"),
        (r"dependency|blocked|blocker", "Dependency"),
        (r"approval|decision", "Approval"),
        (r"resource|staff|capacity", "Resources"),
        (r"scope|requirement|deliverable", "Scope"),
        (r"quality|defect|testing", "Quality"),
    ):
        if re.search(pattern, text):
            return category
    return "General"


@router.get("")
def project_insights() -> dict[str, Any]:
    try:
        with connect() as connection:
            document_rows = connection.execute(
                """SELECT id, processing_status, is_current_version
                   FROM documents"""
            ).fetchall()
        current_document_ids = [
            row["id"] for row in document_rows
            if row["processing_status"] == "indexed" and row["is_current_version"]
        ]
        health = calculate_project_health(persist=False)
        scope = analyze_scope(persist=False, use_llm=False)
        risks = analyze_risks(persist=False, use_llm=False)
        blockers = analyze_blockers(persist=False, use_llm=False)
        today = datetime.now(timezone.utc).date()
        actions = []
        for item in blockers["action_items"]:
            actions.append({
                **item,
                "status": _action_status(item, today),
                "source_status": item.get("status"),
            })
        blocker_items = blockers["blockers"]
        unresolved_blockers = [item for item in blocker_items if _is_unresolved_blocker(item)]
        risk_items = [
            {
                **item,
                "category": _risk_category(item),
                "probability": item.get("likelihood"),
                "impact": item.get("impact"),
                "mitigation": item.get("recommended_mitigation") or (item.get("recommendations") or [None])[0],
                "source_document": (
                    (item.get("evidence") or [{}])[0].get("filename")
                    or ((item.get("evidence") or [{}])[0].get("source"))
                ),
            }
            for item in risks["findings"]
        ]
        milestone_counts = {
            status: 0
            for status in (
                "completed", "in_progress", "delayed", "blocked",
                "at_risk", "pending", "not_stated",
            )
        }
        for milestone in scope["milestones"]:
            milestone_counts[_milestone_status(milestone, today)] += 1
        generation = (
            get_latest_documentation(current_document_ids)
            if current_document_ids else None
        )
        documentation_counts = {
            key: len(generation.get(key, [])) if generation else 0
            for key in ("user_stories", "risk_register", "action_items", "pending_decisions")
        }
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "project_status": health["classification"],
            "metrics": {
                "total_uploaded_documents": len(document_rows),
                "processed_documents": sum(
                    row["processing_status"] == "indexed" for row in document_rows
                ),
                "current_indexed_documents": len(current_document_ids),
                "total_risks": len(risk_items),
                "total_blockers": len(blocker_items),
                "total_actions": len(actions),
                "total_deliverables": len(scope["deliverables"]),
                "overall_project_health_score": health["overall_score"],
                "documentation_generated_items": (
                    generation["total_generated_items"] if generation else 0
                ),
            },
            "health": health,
            "risks": risk_items,
            "blockers": blocker_items,
            "actions": actions,
            "documentation": {
                "is_current": generation is not None,
                "generation_id": generation.get("id") if generation else None,
                "generated_at": generation.get("generated_at") if generation else None,
                "source_documents": generation.get("source_documents", []) if generation else [],
                "counts": documentation_counts,
                "total_generated_items": (
                    generation["total_generated_items"] if generation else 0
                ),
            },
            "scope": {
                "goals": scope["goals"],
                "objectives": scope["objectives"],
                "deliverables": scope["deliverables"],
                "milestones": scope["milestones"],
                "responsibilities": scope["responsibilities"],
                "dependencies": scope["dependencies"],
                "progress": scope["progress"],
                "missing_information": scope["missing_information"],
                "source_documents": scope["source_documents"],
            },
            "action_counts": {
                "total": len(actions),
                "pending": sum(item["status"] == "pending" for item in actions),
                "completed": sum(item["status"] == "completed" for item in actions),
                "overdue": sum(item["status"] == "overdue" for item in actions),
            },
            "risk_counts": {
                level: sum((item["level"] or "Unrated") == level for item in risk_items)
                for level in ("Critical", "High", "Medium", "Low", "Unrated")
            },
            "blocker_counts": {
                "total": len(blocker_items),
                "unresolved": len(unresolved_blockers),
                "resolved": len(blocker_items) - len(unresolved_blockers),
                "pending_decisions": len(blockers["pending_decisions"]),
            },
            "milestone_counts": {
                **milestone_counts,
                "total": len(scope["milestones"]),
            },
        }
    except Exception as error:
        logger.exception("Project insights could not be generated")
        raise HTTPException(status_code=503, detail="Project insights could not be generated.") from error
