import json
import re
import uuid
from datetime import date, datetime, timezone
from typing import Any

from app.database.db import connect
from app.services.project_agents import analyze_blockers, analyze_risks, analyze_scope


WEIGHTS = {
    "scope_clarity": 0.35,
    "timeline_health": 0.35,
    "blocker_health": 0.30,
}


def _classify_score(score: float) -> str:
    if score >= 80:
        return "Healthy"
    if score >= 60:
        return "Needs Attention"
    return "At Risk"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _scope_dimension(scope: dict[str, Any], document_text: str) -> dict[str, Any]:
    requirements = bool(re.search(
        r"\b(requirement|user story|acceptance criteria|shall|must be able to)\b",
        document_text, flags=re.IGNORECASE,
    ))
    checks = [
        ("Project goals", bool(scope["goals"])),
        ("Defined deliverables", bool(scope["deliverables"])),
        ("Milestones", bool(scope["milestones"])),
        ("Assigned responsibilities", bool(scope["responsibilities"])),
        ("Explicit requirements", requirements),
    ]
    score = sum(20 for _, present in checks if present)
    factors = [
        f"{label}: {'identified' if present else 'not identified'}"
        for label, present in checks
    ]
    return {
        "name": "Scope Clarity",
        "score": float(score),
        "available": True,
        "explanation": "20 points are awarded for each documented scope element: goals, deliverables, milestones, responsibilities, and requirements.",
        "factors": factors,
    }


def _parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    for date_format in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(value.strip(), date_format).date()
        except ValueError:
            continue
    return None


def _timeline_dimension(
    scope: dict[str, Any],
    risks: dict[str, Any],
    today: date | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    current_date = today or datetime.now(timezone.utc).date()
    milestones = scope["milestones"]
    dated_milestones = [item for item in milestones if _parse_iso_date(item.get("date"))]
    delayed: list[dict[str, Any]] = []
    for milestone in milestones:
        description = f"{milestone.get('name') or ''} {milestone.get('description') or ''} {milestone.get('status') or ''}"
        due_date = _parse_iso_date(milestone.get("date"))
        explicit_delay = bool(re.search(r"\b(delayed|overdue|behind schedule)\b", description, flags=re.IGNORECASE))
        incomplete_status = bool(re.search(
            r"\b(in progress|pending|blocked|not started|at risk|open)\b",
            description,
            flags=re.IGNORECASE,
        ))
        date_overdue = (
            due_date is not None
            and due_date < current_date
            and incomplete_status
            and not re.search(r"\b(completed|done|closed|delivered)\b", description, flags=re.IGNORECASE)
        )
        if explicit_delay or date_overdue:
            delayed.append(milestone)

    schedule_risks = [
        item for item in risks["findings"]
        if re.search(r"\b(schedule|delay|delayed|overdue|deadline|timeline|milestone)\b",
                     f"{item['title']} {item['description']}", flags=re.IGNORECASE)
    ]
    dependency_risks = [
        item for item in risks["findings"]
        if re.search(r"\b(dependency|blocked|waiting|approval)\b", item["description"], flags=re.IGNORECASE)
    ]
    timeline_evidence = bool(milestones or schedule_risks or dependency_risks)
    factors: list[str] = []
    if not timeline_evidence:
        return ({
            "name": "Timeline Health",
            "score": None,
            "available": False,
            "explanation": "No milestone, deadline, schedule-risk, or timeline evidence was found; a score cannot be determined.",
            "factors": ["Timeline evidence is insufficient."],
        }, [])

    score = 100
    if delayed:
        penalty = min(60, 20 * len(delayed))
        score -= penalty
        factors.append(f"{len(delayed)} delayed or overdue milestone(s): -{penalty} points.")
    else:
        factors.append("No delayed or overdue milestone evidence found: no delay penalty.")
    if schedule_risks:
        penalty = min(30, 12 * len(schedule_risks))
        score -= penalty
        factors.append(f"{len(schedule_risks)} schedule risk finding(s): -{penalty} points.")
    if dependency_risks:
        penalty = min(20, 10 * len(dependency_risks))
        score -= penalty
        factors.append(f"{len(dependency_risks)} dependency/approval risk finding(s): -{penalty} points.")
    if milestones and not dated_milestones:
        score -= 10
        factors.append("Milestones were identified but no parseable milestone dates were found: -10 points.")
    elif dated_milestones:
        factors.append(f"{len(dated_milestones)} milestone date(s) are available.")

    return ({
        "name": "Timeline Health",
        "score": float(max(0, score)),
        "available": True,
        "explanation": "Starts at 100; explicit delayed/overdue milestones cost 20 points each (max 60), schedule risks cost 12 each (max 30), dependency/approval risks cost 10 each (max 20), and milestones without parseable dates cost 10.",
        "factors": factors,
    }, delayed)


def _blocker_dimension(
    blockers: dict[str, Any],
    document_text: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    unresolved = [
        item for item in blockers["blockers"]
        if (item.get("status") or "").lower() not in {"resolved", "closed", "completed", "done"}
        and not re.search(r"\b(resolved|closed|completed|done)\b", item["description"], flags=re.IGNORECASE)
        and not re.search(
            r"\b(?:no|zero)\s+(?:open\s+)?blockers?\b|\bno unresolved blockers?\b",
            item["description"],
            flags=re.IGNORECASE,
        )
    ]
    decisions = [
        item for item in blockers["pending_decisions"]
        if (item.get("status") or "").lower() not in {"resolved", "closed", "completed", "done"}
        and not re.search(r"\b(decided|resolved|closed|completed)\b", item["description"], flags=re.IGNORECASE)
    ]
    blocked_actions = [
        item for item in blockers["action_items"]
        if (item.get("status") or "").lower() == "blocked"
        or re.search(r"\bblocked task\b", item["description"], flags=re.IGNORECASE)
    ]
    explicit_clear = bool(re.search(
        r"\b(no|zero)\s+(?:open\s+)?blockers?\b|\bno unresolved blockers?\b",
        document_text, flags=re.IGNORECASE,
    ))
    has_evidence = bool(unresolved or decisions or blocked_actions or explicit_clear)
    if not has_evidence:
        return ({
            "name": "Blocker Health",
            "score": None,
            "available": False,
            "explanation": "No blocker/decision status or explicit no-blocker statement was found; absence of findings is not treated as zero blockers.",
            "factors": ["Blocker status is insufficiently documented."],
        }, unresolved, decisions)

    penalty = min(70, 25 * len(unresolved))
    decision_penalty = min(30, 10 * len(decisions))
    blocked_task_penalty = min(40, 20 * len(blocked_actions))
    score = max(0, 100 - penalty - decision_penalty - blocked_task_penalty)
    factors = [
        f"{len(unresolved)} unresolved blocker(s): -{penalty} points.",
        f"{len(decisions)} pending decision(s): -{decision_penalty} points.",
        f"{len(blocked_actions)} explicitly blocked task(s): -{blocked_task_penalty} points.",
    ]
    if explicit_clear and not unresolved:
        factors.append("Source explicitly reports no unresolved blockers.")
    return ({
        "name": "Blocker Health",
        "score": float(score),
        "available": True,
        "explanation": "Starts at 100; each unresolved blocker costs 25 points (max 70), each pending decision costs 10 (max 30), and each explicitly blocked task costs 20 (max 40). Ordinary action items are not counted as blockers.",
        "factors": factors,
    }, unresolved, decisions)


def calculate_project_health(persist: bool = True) -> dict[str, Any]:
    with connect() as connection:
        documents = connection.execute(
            """SELECT id, filename, extracted_text FROM documents
               WHERE processing_status = 'indexed'
                 AND is_current_version = 1
                 AND NOT (
                     lower(filename) LIKE '%readme%'
                     AND (lower(filename) LIKE '%test%' OR lower(filename) LIKE '%guide%')
                 )
               ORDER BY uploaded_at DESC"""
        ).fetchall()
    document_rows = [dict(row) for row in documents]
    document_ids = [item["id"] for item in document_rows]
    document_text = "\n".join(item["extracted_text"] or "" for item in document_rows)
    calculated_at = _utc_now()

    if not document_rows:
        result = {
            "id": str(uuid.uuid4()),
            "calculated_at": calculated_at,
            "overall_score": None,
            "classification": "Insufficient Data",
            "dimensions": {
                key: {
                    "name": name, "score": None, "available": False,
                    "explanation": "No indexed documents are available for this dimension.",
                    "factors": ["Upload and index project documents to calculate this score."],
                }
                for key, name in (
                    ("scope_clarity", "Scope Clarity"),
                    ("timeline_health", "Timeline Health"),
                    ("blocker_health", "Blocker Health"),
                )
            },
            "weights": WEIGHTS,
            "identified_risks": [],
            "unresolved_blockers": [],
            "pending_decisions": [],
            "delayed_milestones": [],
            "recommended_actions": ["Upload and index project documents before calculating project health."],
            "document_count": 0,
        }
        if persist:
            with connect() as connection:
                connection.execute(
                    "INSERT INTO project_health_scores (id, created_at, result_json) VALUES (?, ?, ?)",
                    (result["id"], calculated_at, json.dumps(result, ensure_ascii=False)),
                )
        return result

    scope = analyze_scope(document_ids=document_ids, persist=False, use_llm=False)
    risks = analyze_risks(document_ids=document_ids, persist=False, use_llm=False)
    blockers = analyze_blockers(document_ids=document_ids, persist=False, use_llm=False)
    scope_dimension = _scope_dimension(scope, document_text)
    timeline_dimension, delayed = _timeline_dimension(scope, risks)
    blocker_dimension, unresolved, decisions = _blocker_dimension(blockers, document_text)
    dimensions = {
        "scope_clarity": scope_dimension,
        "timeline_health": timeline_dimension,
        "blocker_health": blocker_dimension,
    }
    if all(item["score"] is not None for item in dimensions.values()):
        overall_score = round(sum(
            float(dimensions[key]["score"]) * weight for key, weight in WEIGHTS.items()
        ), 1)
        classification = _classify_score(overall_score)
    else:
        overall_score = None
        classification = "Insufficient Data"

    recommended_actions: list[str] = []
    for item in unresolved[:3]:
        description = item.get("recommendation") or item.get("description")
        if description and description not in recommended_actions:
            recommended_actions.append(description)
    for item in decisions[:3]:
        suggestion = f"Resolve pending decision: {item['description']}"
        if suggestion not in recommended_actions:
            recommended_actions.append(suggestion)
    if scope_dimension["score"] < 60:
        recommended_actions.append("Clarify project goals, deliverables, milestones, owners, and explicit requirements.")
    if timeline_dimension["score"] is not None and timeline_dimension["score"] < 80:
        recommended_actions.append("Review delayed milestones and schedule dependencies with their owners.")
    if not recommended_actions:
        recommended_actions.append("Continue updating project records so future health calculations reflect current status.")

    result = {
        "id": str(uuid.uuid4()),
        "calculated_at": calculated_at,
        "overall_score": overall_score,
        "classification": classification,
        "dimensions": dimensions,
        "weights": WEIGHTS,
        "identified_risks": risks["findings"][:20],
        "unresolved_blockers": unresolved[:20],
        "pending_decisions": decisions[:20],
        "delayed_milestones": delayed[:20],
        "recommended_actions": recommended_actions,
        "document_count": len(document_rows),
    }
    if persist:
        with connect() as connection:
            connection.execute(
                "INSERT INTO project_health_scores (id, created_at, result_json) VALUES (?, ?, ?)",
                (result["id"], calculated_at, json.dumps(result, ensure_ascii=False)),
            )
    return result


def get_latest_project_health() -> dict[str, Any] | None:
    with connect() as connection:
        row = connection.execute(
            "SELECT result_json FROM project_health_scores ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    return json.loads(row["result_json"]) if row else None
