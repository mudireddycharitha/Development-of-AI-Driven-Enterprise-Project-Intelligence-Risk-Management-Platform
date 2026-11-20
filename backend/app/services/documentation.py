import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from app.database.db import connect
from app.services.project_agents import (
    _build_reference,
    _load_chunks_for_document,
    _load_documents,
    _sentence_split,
    analyze_blockers,
    analyze_risks,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _selected_documents(document_ids: list[str] | None) -> list[dict[str, Any]]:
    if document_ids is not None:
        selected_ids = list(dict.fromkeys(item.strip() for item in document_ids if item.strip()))
        if not selected_ids:
            raise ValueError("Select at least one indexed document.")
        if len(selected_ids) > 100:
            raise ValueError("Select no more than 100 documents at a time.")
        documents = _load_documents(document_ids=selected_ids)
        if len(documents) != len(selected_ids):
            raise LookupError("One or more selected documents do not exist.")
        if any(document["processing_status"] != "indexed" for document in documents):
            raise ValueError("All selected documents must be indexed before generation.")
        return documents
    return _load_documents()


def _stable_id(prefix: str, document_id: str, excerpt: str) -> str:
    digest = hashlib.sha1(f"{document_id}\n{excerpt}".encode("utf-8")).hexdigest()[:10]
    return f"{prefix}-{digest}"


def _priority(text: str) -> str | None:
    match = re.search(r"\b(critical|high|medium|low)\s+priority\b|\bpriority\s*[:=]\s*(critical|high|medium|low)\b",
                      text, flags=re.IGNORECASE)
    if not match:
        return None
    return next(group for group in match.groups() if group).title()


def _user_stories(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    stories: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    explicit_story = re.compile(
        r"\bAs\s+(?:a|an)\s+(?P<role>[^,.;]{2,60}),?\s*"
        r"I\s+(?:want|need)\s+(?P<feature>[^.;]+)",
        flags=re.IGNORECASE,
    )
    role_request = re.compile(
        r"\b(?P<role>[A-Z][A-Za-z0-9 /&'-]{1,45}?)\s+"
        r"(?:requested|need|needs|want|wants|require|requires)\s+"
        r"(?:a way to|the ability to|to)\s+(?P<feature>[^.;]+)",
        flags=re.IGNORECASE,
    )
    requirement_statement = re.compile(
        r"\b(?:user requirement|feature request|requirement)\s*[:\-]\s*(?P<feature>[^.;]+)",
        flags=re.IGNORECASE,
    )
    system_need = re.compile(
        r"\b(?:users?|customers?|students?|employees?|managers?|teams?)\s+"
        r"(?:must|should|need to|needs to|want to)\s+be able to\s+(?P<feature>[^.;]+)",
        flags=re.IGNORECASE,
    )
    action_only = re.compile(
        r"^(?:approve|provide|deliver|complete|finish|implement|review|validate|"
        r"submit|prepare|send|resolve|test|create|investigate|schedule|confirm)\b",
        flags=re.IGNORECASE,
    )

    for document in documents:
        chunks = _load_chunks_for_document(document["id"])
        acceptance = [
            sentence for sentence in _sentence_split(document.get("extracted_text") or "")
            if re.search(r"\bacceptance criteria?\b", sentence, flags=re.IGNORECASE)
        ]
        for sentence in _sentence_split(document.get("extracted_text") or ""):
            match = explicit_story.search(sentence)
            role = match.group("role").strip() if match else None
            feature = match.group("feature").strip(" ,:") if match else None
            benefit = None
            if feature and " so that " in feature.lower():
                feature, benefit = re.split(r"\s+so that\s+", feature, maxsplit=1, flags=re.IGNORECASE)

            if not feature:
                match = role_request.search(sentence)
                if match and not action_only.match(match.group("feature").strip()):
                    role = match.group("role").strip()
                    feature = match.group("feature").strip(" ,:")
            if not feature:
                match = system_need.search(sentence)
                if match:
                    role = match.group(0).split()[0]
                    feature = match.group("feature").strip(" ,:")
            if not feature:
                match = requirement_statement.search(sentence)
                if match:
                    role = "project user"
                    feature = match.group("feature").strip(" ,:")
            if not feature:
                continue

            if benefit is None and re.search(r"\s+so that\s+", feature, flags=re.IGNORECASE):
                feature, benefit = re.split(r"\s+so that\s+", feature, maxsplit=1, flags=re.IGNORECASE)
            feature = re.sub(r"\s+", " ", feature).strip(" .")
            if len(feature) < 4:
                continue
            key = (document["id"], sentence.strip())
            if key in seen:
                continue
            seen.add(key)
            reference = _build_reference(document, sentence, chunks)
            story_role = role or "project user"
            feature_terms = {
                term for term in re.findall(r"[a-z0-9]{4,}", feature.lower())
                if term not in {"that", "with", "from", "their", "they", "this"}
            }
            story_criteria = [
                criterion for criterion in acceptance
                if feature_terms.intersection(re.findall(r"[a-z0-9]{4,}", criterion.lower()))
            ]
            stories.append({
                "id": _stable_id("US", document["id"], sentence),
                "user_role": story_role,
                "requirement": feature,
                "expected_benefit": benefit.strip(" .") if benefit else
                    "fulfil the requirement recorded in the source document",
                "title": f"{story_role} requirement",
                "description": f"As a {story_role}, I want {feature} so that "
                    f"{benefit.strip(' .') if benefit else 'I can fulfil the documented requirement'}.",
                "priority": _priority(sentence),
                "acceptance_criteria": story_criteria,
                "source_document": document["filename"],
                "evidence": reference.model_dump(mode="json"),
                "status": "Identified",
            })
    return stories


def _risk_category(text: str) -> str:
    lowered = text.lower()
    for category, terms in (
        ("Schedule", ("schedule", "delay", "delayed", "overdue", "deadline", "milestone")),
        ("Dependency", ("dependency", "depends", "blocked", "waiting", "approval")),
        ("Resource", ("resource", "capacity", "staff", "team shortage")),
        ("Technical", ("technical", "api", "integration", "software", "security", "infrastructure")),
        ("Quality", ("quality", "defect", "testing", "test failure")),
        ("Requirement", ("requirement", "scope", "unclear", "change request")),
    ):
        if any(term in lowered for term in terms):
            return category
    return "Project"


def _explicit_likelihood(text: str) -> str:
    if re.search(r"\b(not likely|unlikely)\b", text, flags=re.IGNORECASE):
        return "Unlikely"
    match = re.search(
        r"\b(?:likelihood\s*(?:is|:)?\s*|)(very likely|likely|possible|very unlikely)\b",
        text, flags=re.IGNORECASE,
    )
    return match.group(1).title() if match else "Not stated in source"


def _explicit_impact(text: str) -> str:
    match = re.search(
        r"\b(?:impact|severity)\s*(?:is|:)?\s*(critical|high|medium|low)\b|"
        r"\b(critical|high|medium|low)\s+impact\b",
        text, flags=re.IGNORECASE,
    )
    return next(group for group in match.groups() if group).title() if match else "Not stated in source"


def _responsible_person(text: str) -> str | None:
    for pattern in (
        r"\b(?:risk\s+)?owner\s*[:=]\s*([A-Z][A-Za-z .'-]{1,40})",
        r"\bassigned to\s+([A-Z][A-Za-z .'-]{1,40})",
        r"\bowned by\s+([A-Z][A-Za-z .'-]{1,40})",
        r"\bresponsible person\s*(?:is|:)?\s*([A-Z][A-Za-z .'-]{1,40})",
    ):
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip().rstrip(".,;")
    return None


def _risk_level(text: str, agent_level: str) -> str:
    explicit = re.search(
        r"\b(?:risk\s+level|severity)\s*[:=]\s*(critical|high|medium|low)\b|"
        r"\b(critical|high|medium|low)\s+risk\b",
        text, flags=re.IGNORECASE,
    )
    if explicit:
        return next(group for group in explicit.groups() if group).title()
    lowered = text.lower()
    if any(term in lowered for term in ("cannot proceed", "hard blocker", "cancelled", "critical")):
        return "Critical"
    if any(term in lowered for term in ("overdue", "blocked", "dependency gap", "no approval", "resource shortage")):
        return "High"
    if any(term in lowered for term in (
        "delay", "delayed", "schedule", "dependency", "approval pending", "risk",
        "not available", "unavailable", "waiting for", "waiting on",
    )):
        return "Medium"
    return agent_level


def _risk_register(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ids = [document["id"] for document in documents]
    analysis = analyze_risks(document_ids=ids)
    result: list[dict[str, Any]] = []
    for finding in analysis["findings"]:
        evidence = finding.get("evidence") or []
        if not evidence:
            continue
        reference = evidence[0]
        result.append({
            "id": _stable_id("RISK", reference.get("document_id") or "", finding["description"]),
            "title": finding["title"],
            "description": finding["description"],
            "category": _risk_category(finding["description"]),
            "likelihood": _explicit_likelihood(finding["description"]),
            "impact": _explicit_impact(finding["description"]),
            "risk_level": _risk_level(finding["description"], finding["level"]),
            "suggested_mitigation": (finding.get("recommendations") or [""])[0],
            "responsible_person": _responsible_person(finding["description"]),
            "source_document": reference.get("filename") or "Unknown source",
            "evidence": reference,
            "inference": bool(finding.get("inference")),
        })
    seen = {
        (item["evidence"].get("document_id"), item["description"].casefold())
        for item in result
    }
    for document in documents:
        chunks = _load_chunks_for_document(document["id"])
        for sentence in _sentence_split(document.get("extracted_text") or ""):
            if not re.search(
                r"\b(?:not available|unavailable|waiting for|waiting on|blocked by)\b",
                sentence, flags=re.IGNORECASE,
            ):
                continue
            key = (document["id"], sentence.casefold())
            if key in seen:
                continue
            seen.add(key)
            reference = _build_reference(document, sentence, chunks)
            if not reference.excerpt:
                continue
            result.append({
                "id": _stable_id("RISK", document["id"], sentence),
                "title": "Dependency availability risk",
                "description": sentence,
                "category": "Dependency",
                "likelihood": _explicit_likelihood(sentence),
                "impact": _explicit_impact(sentence),
                "risk_level": _risk_level(sentence, "Not stated"),
                "suggested_mitigation": "Confirm the dependency owner and expected availability date before dependent work proceeds.",
                "responsible_person": _responsible_person(sentence),
                "source_document": document["filename"],
                "evidence": reference.model_dump(mode="json"),
                "inference": True,
            })
    return result


def _actions_and_decisions(documents: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    analysis = analyze_blockers(document_ids=[document["id"] for document in documents])
    actions: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    for item in analysis["action_items"]:
        evidence = (item.get("evidence") or [None])[0]
        if evidence is None:
            continue
        actions.append({
            "id": _stable_id("ACT", evidence.get("document_id") or "", item["description"]),
            "description": item["description"],
            "assignee": item.get("assignee"),
            "due_date": item.get("due_date"),
            "priority": item.get("priority"),
            "status": _documented_action_status(item["description"]),
            "related_project_milestone": item.get("related_milestone"),
            "source_document": evidence.get("filename") or item.get("source_document") or "Unknown source",
            "evidence": evidence,
        })
    for item in analysis["pending_decisions"]:
        evidence = (item.get("evidence") or [None])[0]
        if evidence is None:
            continue
        decisions.append({
            "id": _stable_id("DEC", evidence.get("document_id") or "", item["description"]),
            "description": item["description"],
            "responsible_person": item.get("assignee") or _decision_owner(item["description"]),
            "related_project_area": item.get("related_task") or item.get("related_milestone"),
            "source_document": evidence.get("filename") or item.get("source_document") or "Unknown source",
            "evidence": evidence,
        })
    return actions, decisions


def _documented_action_status(description: str) -> str:
    if re.search(r"\b(completed|complete|done|resolved|closed)\b", description, flags=re.IGNORECASE):
        return "Completed"
    if re.search(r"\b(in progress|ongoing)\b", description, flags=re.IGNORECASE):
        return "In Progress"
    if re.search(r"\b(blocked|waiting for|waiting on|awaiting)\b", description, flags=re.IGNORECASE):
        return "Blocked"
    if re.search(r"\b(pending|open)\b", description, flags=re.IGNORECASE):
        return "Pending"
    return "Not stated"


def _decision_owner(description: str) -> str | None:
    match = re.search(
        r"(?:^|:\s*)(?:the\s+)?([A-Za-z][A-Za-z'-]*(?:\s+[A-Za-z][A-Za-z'-]*){0,2})\s+"
        r"(?:must|should|needs? to|has to)\s+(?:decide|approve|select|confirm)\b",
        description, flags=re.IGNORECASE,
    )
    if not match:
        return None
    return re.sub(r"^the\s+", "", match.group(1), flags=re.IGNORECASE)


def generate_documentation(document_ids: list[str] | None = None) -> dict[str, Any]:
    documents = _selected_documents(document_ids)
    if not documents:
        raise ValueError("No indexed documents are available for documentation generation.")
    selected_ids = sorted(document["id"] for document in documents)
    generation_key = hashlib.sha256(
        json.dumps(selected_ids, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    user_stories = _user_stories(documents)
    risk_register = _risk_register(documents)
    action_items, pending_decisions = _actions_and_decisions(documents)
    generated_at = _utc_now()
    with connect() as connection:
        existing = connection.execute(
            "SELECT id FROM documentation_generations WHERE generation_key = ?",
            (generation_key,),
        ).fetchone()
        generation_id = existing["id"] if existing else str(uuid.uuid4())
        payload = {
            "id": generation_id,
            "generated_at": generated_at,
            "document_ids": selected_ids,
            "source_documents": sorted({document["filename"] for document in documents}),
            "user_stories": user_stories,
            "risk_register": risk_register,
            "action_items": action_items,
            "pending_decisions": pending_decisions,
            "total_generated_items": (
                len(user_stories) + len(risk_register) + len(action_items) + len(pending_decisions)
            ),
        }
        connection.execute(
            """INSERT INTO documentation_generations
               (id, generation_key, document_ids, generated_at, result_json)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(generation_key) DO UPDATE SET
                   document_ids = excluded.document_ids,
                   generated_at = excluded.generated_at,
                   result_json = excluded.result_json""",
            (generation_id, generation_key, json.dumps(selected_ids), generated_at,
             json.dumps(payload, ensure_ascii=False)),
        )
    return payload


def get_latest_documentation(document_ids: list[str] | None = None) -> dict[str, Any] | None:
    with connect() as connection:
        if document_ids is None:
            row = connection.execute(
                "SELECT result_json FROM documentation_generations ORDER BY generated_at DESC LIMIT 1"
            ).fetchone()
        else:
            selected_ids = sorted(set(item.strip() for item in document_ids if item.strip()))
            if not selected_ids:
                return None
            generation_key = hashlib.sha256(
                json.dumps(selected_ids, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            row = connection.execute(
                "SELECT result_json FROM documentation_generations WHERE generation_key = ?",
                (generation_key,),
            ).fetchone()
    return json.loads(row["result_json"]) if row else None


def get_documentation(generation_id: str) -> dict[str, Any] | None:
    with connect() as connection:
        row = connection.execute(
            "SELECT result_json FROM documentation_generations WHERE id = ?",
            (generation_id,),
        ).fetchone()
    return json.loads(row["result_json"]) if row else None
