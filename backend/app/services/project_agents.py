import json
import logging
import re
import threading
import uuid
from collections import OrderedDict
from concurrent.futures import Future
from functools import lru_cache
from datetime import datetime, timezone
from time import perf_counter, sleep
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
    MainProjectGoal,
    RiskAnalysis,
    RiskFinding,
    normalize_risk_level,
    ScopeAnalysis,
    ScopeDependency,
    ScopeFinding,
    ScopeMilestone,
    ScopeResponsibility,
    SupportingObjective,
)

logger = logging.getLogger(__name__)
_AGENT_CONTEXT_CACHE: OrderedDict[tuple[Any, ...], str] = OrderedDict()
_AGENT_CONTEXT_CACHE_LOCK = threading.RLock()
_AGENT_CONTEXT_CACHE_LIMIT = 24
_STRUCTURED_AGENT_CACHE: OrderedDict[tuple[Any, ...], str] = OrderedDict()
_STRUCTURED_AGENT_CACHE_LOCK = threading.RLock()
_STRUCTURED_AGENT_CACHE_LIMIT = 48
_STRUCTURED_AGENT_IN_FLIGHT: dict[tuple[Any, ...], Future[dict[str, Any]]] = {}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip()


def _document_version_key(documents: list[dict[str, Any]]) -> tuple[tuple[str, ...], ...]:
    return tuple(sorted((
        str(document.get("id") or ""),
        str(document.get("sha256") or ""),
        str(document.get("updated_at") or ""),
        str(document.get("document_version") or ""),
        str(document.get("indexed_chunk_count") or ""),
    ) for document in documents))


def _sentence_split(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [item.strip() for item in parts if item and item.strip()]


def _is_structural_heading(text: str) -> bool:
    normalized = _normalize_text(text).lstrip("# ").casefold()
    if not normalized or len(normalized) < 8:
        return True
    return bool(re.match(
        r"^(?:worksheet:|table row:|risk id:|action id:|dependency id:|dependency:|deliverables and milestones$|out of scope$|"
        r"scope and deliverables$|problem and objective$|actions and next steps$|"
        r"risks, blocker, and decision$|open issues$|risk register$|action items$|"
        r"dependencies$|milestones$|requirements$|project scope$|scope$|goal$|known blocker$|"
        r"project goals?$|project objectives?$|success criteria$|objective$|problem statement$|expected outcome$|project outcome$|"
        r"team responsibilities$|owner:|status:|impact$|likelihood$|"
        r"mitigation$|due date$|source:|page \d+$|slide \d+$)",
        normalized,
    ))


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
        r"\b\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4}\b",
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
                r"(?im)^\s*(Deliverable|Due date|Status|Owner):\s*(.*?)\s*$",
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
            owner=fields.get("owner"),
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


def _analysis_documents(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop uploaded validation instructions, which describe expected agent answers."""
    eligible = [
        doc for doc in documents
        if not re.search(r"readme.*testing.*guide|testing.*guide", doc.get("filename", ""), re.I)
        and "intended to validate all project agents" not in (doc.get("extracted_text") or "").casefold()
    ]
    return eligible or documents


def _structured_register_blocks(document: dict[str, Any]) -> list[dict[str, str]]:
    blocks = []
    for block in re.split(r"\n\s*\n", document.get("extracted_text") or ""):
        fields: dict[str, str] = {}
        for line in block.splitlines():
            match = re.match(r"\s*([^:]+):\s*(.*?)\s*$", line)
            if match and match.group(2):
                fields[match.group(1).casefold()] = match.group(2).strip()
        if fields:
            blocks.append({**fields, "_excerpt": block.strip()})
    return blocks


def _following_labeled_lines(document: dict[str, Any], labels: set[str]) -> list[str]:
    lines = (document.get("extracted_text") or "").splitlines()
    values = []
    for index, line in enumerate(lines):
        label = _normalize_text(line).rstrip(":").casefold()
        if label not in labels:
            continue
        following = next((item.strip() for item in lines[index + 1:] if item.strip()), "")
        if following and not _is_structural_heading(following) and following not in values:
            values.append(following)
    return values


def _dedupe_blocker_items(items: list[BlockerItem]) -> list[BlockerItem]:
    kept: list[BlockerItem] = []
    stop_words = {"the", "a", "an", "and", "to", "for", "of", "in", "on", "is", "are", "be", "by", "must", "will", "should", "team", "action", "item", "pending", "decision", "which", "whether", "use"}
    items = sorted(items, key=lambda item: (
        -bool(item.assignee), -bool(item.due_date), -bool(item.priority),
        -bool(item.evidence and item.evidence[0].location.get("sheet_name")),
    ))
    topics: set[str] = set()
    for item in items:
        text = item.description.casefold()
        topic = next((label for term, label in (
            ("authentication", "authentication"), ("identity-provider", "authentication"),
            ("sign-in", "authentication"), ("notification", "notifications"),
            ("email", "notifications"), ("in-app", "notifications"), ("attendance", "attendance data"),
            ("spreadsheet", "attendance data"), ("student id", "attendance data"),
        ) if term in text), None)
        if topic and topic in topics and item.category != "action_item":
            continue
        words = set(re.findall(r"[a-z0-9]+", item.description.casefold())) - stop_words
        if not words:
            continue
        duplicate = False
        for previous in kept:
            if previous.assignee and item.assignee:
                previous_owner = re.split(r"\s*/\s*|\s+", _normalize_text(previous.assignee), maxsplit=1)[0].casefold()
                item_owner = re.split(r"\s*/\s*|\s+", _normalize_text(item.assignee), maxsplit=1)[0].casefold()
                if previous_owner != item_owner:
                    continue
            old_words = set(re.findall(r"[a-z0-9]+", previous.description.casefold())) - stop_words
            shared = words & old_words
            threshold = 0.66 if item.category == "action_item" else 0.84
            if shared and len(shared) / min(len(words), len(old_words)) >= threshold:
                duplicate = True
                break
        if not duplicate:
            kept.append(item)
            if topic and item.category != "action_item":
                topics.add(topic)
    return kept


def _dedupe_risk_findings(items: list[RiskFinding]) -> list[RiskFinding]:
    kept: list[RiskFinding] = []
    topics: set[tuple[str, str]] = set()
    items = sorted(items, key=lambda item: (
        0 if item.category == "Risk register" else 1 if item.category == "Dependency gap" else 2,
        -len(item.description),
    ))
    for item in items:
        description = item.description.casefold()
        topic = next((label for term, label in (
            ("auth", "authentication"), ("identity-provider", "authentication"),
            ("sign-in", "authentication"), ("student id", "student identifiers"),
            ("spreadsheet", "student identifiers"), ("prototype review", "prototype review"),
            ("notification", "notifications"), ("email service", "notifications"),
        ) if term in description), None)
        category_key = "dependency" if item.category == "Dependency gap" else "risk"
        if topic and (category_key, topic) in topics:
            continue
        words = set(re.findall(r"[a-z0-9]+", item.description.casefold())) - {"risk", "may", "be", "the", "a", "to", "of", "in", "for"}
        if not words:
            continue
        if any(
            len(words & (set(re.findall(r"[a-z0-9]+", previous.description.casefold())) - {"risk", "may", "be", "the", "a", "to", "of", "in", "for"}))
            / max(1, min(len(words), len(set(re.findall(r"[a-z0-9]+", previous.description.casefold())) - {"risk", "may", "be", "the", "a", "to", "of", "in", "for"}))) >= 0.6
            for previous in kept
        ):
            continue
        kept.append(item)
        if topic:
            topics.add((category_key, topic))
    return kept


def _agent_evidence(
    documents: list[dict[str, Any]], source: str, quote: str,
) -> EvidenceReference | None:
    """Only attach a citation if the model's quote occurs in the named source."""
    quote = _normalize_text(quote)
    if len(quote) < 12 or not source.strip():
        return None
    for document in documents:
        filename = document.get("filename") or document.get("id") or "unknown"
        if source and source.casefold() not in {filename.casefold(), str(document.get("id", "")).casefold()}:
            continue
        text = _normalize_text(document.get("extracted_text"))
        if quote.casefold() not in text.casefold():
            continue
        chunks = _load_chunks_for_document(document["id"])
        reference = _build_reference(document, quote, chunks)
        if reference.excerpt:
            return reference
    return None


def _agent_context(documents: list[dict[str, Any]], agent: str) -> str:
    retrieval_queries = {
        "Scope and Deliverable Extraction Agent": (
            "Project goals objectives scope included and excluded activities requirements constraints "
            "deliverables owners milestones due dates responsibilities dependencies"
        ),
        "Risk Detection and Delivery Forecasting Agent": (
            "Documented project risks impact likelihood mitigation owner severity dependency gaps "
            "delayed milestones schedule challenges approvals and delivery status"
        ),
        "Blocker and Action Item Identification Agent": (
            "Open blockers unresolved issues pending decisions actions owners due dates priorities "
            "statuses approvals and dependencies requiring attention"
        ),
    }
    query = retrieval_queries.get(agent)
    if not query or not documents:
        return ""
    versions = _document_version_key(documents)
    cache_key: tuple[Any, ...] = (agent, versions)
    with _AGENT_CONTEXT_CACHE_LOCK:
        cached = _AGENT_CONTEXT_CACHE.get(cache_key)
        if cached is not None:
            _AGENT_CONTEXT_CACHE.move_to_end(cache_key)
            logger.info("Agent retrieval cache hit agent=%s documents=%d context_chars=%d", agent, len(documents), len(cached))
            return cached

    retrieval_started = perf_counter()
    retrieval_timings: dict[str, float] = {}
    # Reuse the indexed vector store and existing assistant retrieval/reranking.
    # This creates one query embedding per agent; it never re-embeds document chunks.
    from app.services.assistant import _relevant_sentences, retrieve_relevant_chunks

    document_ids = [str(document["id"]) for document in documents if document.get("id")]
    chunk_total = sum(int(document.get("indexed_chunk_count") or 0) for document in documents)
    matches = retrieve_relevant_chunks(
        query,
        top_k=min(48, max(1, chunk_total)),
        document_ids=document_ids,
        timings=retrieval_timings,
    )
    counts_by_document: dict[str, int] = {}
    filenames = {str(document.get("id")): str(document.get("filename") or "unknown") for document in documents}
    sections: list[str] = []
    seen_sentences: set[str] = set()
    context_chars = 0
    context_budget = 2600
    for match in matches:
        document_id = str(match.get("metadata", {}).get("document_id") or "")
        per_document_limit = 3 if filenames.get(document_id, "").casefold().endswith((".xlsx", ".xls")) else 2
        if counts_by_document.get(document_id, 0) >= per_document_limit:
            continue
        sentence_matches = _relevant_sentences(match.get("text") or "", query, limit=4)
        selected = []
        for _, sentence in sentence_matches:
            normalized = _normalize_text(sentence)
            key = normalized.casefold()
            if len(normalized) < 14 or key in seen_sentences:
                continue
            selected.append(normalized[:260])
            seen_sentences.add(key)
            if len(selected) >= 1:
                break
        if not selected:
            continue
        filename = filenames.get(document_id) or str(match.get("metadata", {}).get("filename") or "unknown")
        chunk_id = str(match.get("id") or "")
        location = {
            key: value for key, value in match.get("metadata", {}).items()
            if key in {"page_number", "slide_number", "sheet_name", "row_number", "chunk_index"}
        }
        section = (
            f"SOURCE: {filename}; CHUNK: {chunk_id}; LOCATION: "
            f"{json.dumps(location, ensure_ascii=False, separators=(',', ':'))}\n"
            + "\n".join(f"- {sentence}" for sentence in selected)
        )
        if sections and context_chars + len(section) + 2 > context_budget:
            continue
        sections.append(section)
        context_chars += len(section) + 2
        counts_by_document[document_id] = counts_by_document.get(document_id, 0) + 1

    context = "\n\n".join(sections)
    logger.info(
        "Agent retrieval completed agent=%s documents=%d selected_chunks=%d context_chars=%d "
        "duration_ms=%.1f embedding_ms=%.1f vector_ms=%.1f lexical_ms=%.1f rerank_ms=%.1f",
        agent, len(documents), len(matches), len(context), (perf_counter() - retrieval_started) * 1000,
        retrieval_timings.get("query_embedding_ms", 0.0), retrieval_timings.get("vector_retrieval_ms", 0.0),
        retrieval_timings.get("lexical_retrieval_ms", 0.0), retrieval_timings.get("reranking_ms", 0.0),
    )
    with _AGENT_CONTEXT_CACHE_LOCK:
        _AGENT_CONTEXT_CACHE[cache_key] = context
        _AGENT_CONTEXT_CACHE.move_to_end(cache_key)
        while len(_AGENT_CONTEXT_CACHE) > _AGENT_CONTEXT_CACHE_LIMIT:
            _AGENT_CONTEXT_CACHE.popitem(last=False)
    return context


def _run_structured_agent(
    agent: str, documents: list[dict[str, Any]], response_shape: str,
) -> dict[str, Any] | None:
    """Run an evidence-grounded extraction; None means rules-only mode is configured."""
    if settings.ai_provider == "rule_based":
        return None
    cache_key = (agent, _document_version_key(documents), response_shape)
    with _STRUCTURED_AGENT_CACHE_LOCK:
        cached = _STRUCTURED_AGENT_CACHE.get(cache_key)
        if cached is not None:
            _STRUCTURED_AGENT_CACHE.move_to_end(cache_key)
            logger.info("Structured agent cache hit agent=%s documents=%d", agent, len(documents))
            return json.loads(cached)
        future = _STRUCTURED_AGENT_IN_FLIGHT.get(cache_key)
        is_owner = future is None
        if future is None:
            future = Future()
            _STRUCTURED_AGENT_IN_FLIGHT[cache_key] = future
    if not is_owner:
        logger.info("Structured agent request joined in-flight generation agent=%s", agent)
        return json.loads(json.dumps(future.result(), ensure_ascii=False))

    try:
        prompt = f"""You are the {agent}. Extract only facts explicitly supported by the supplied passages.
Return compact valid JSON matching this shape:
{response_shape}

Every item needs the exact source filename and a verbatim quote of at most 18 words.
Ignore testing guides, headings, worksheet labels, and generic document descriptions.
Do not invent facts. Preserve a field only when the quote supports it.
Return the one main goal and up to 8 distinct supporting objectives when requested.
For other arrays, return up to 4 distinct evidence-backed items. Set project-goal or objective relationships
only when the exact related text appears in that finding's quoted source; otherwise leave them null or empty.
No Markdown or commentary."""
        context = _agent_context(documents, agent)
        if not context:
            raise LLMGenerationError(f"{agent} found no relevant indexed passages for structured extraction.")
        raw = maybe_call_llm(prompt, context, structured=True)
        try:
            parsed = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as error:
            raise LLMGenerationError(f"{agent} returned malformed JSON; no analysis was saved.") from error
        if not isinstance(parsed, dict):
            raise LLMGenerationError(f"{agent} returned an invalid structured response.")
        serialized = json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
        with _STRUCTURED_AGENT_CACHE_LOCK:
            _STRUCTURED_AGENT_CACHE[cache_key] = serialized
            _STRUCTURED_AGENT_CACHE.move_to_end(cache_key)
            while len(_STRUCTURED_AGENT_CACHE) > _STRUCTURED_AGENT_CACHE_LIMIT:
                _STRUCTURED_AGENT_CACHE.popitem(last=False)
        future.set_result(parsed)
        return parsed
    except BaseException as error:
        future.set_exception(error)
        raise
    finally:
        with _STRUCTURED_AGENT_CACHE_LOCK:
            if _STRUCTURED_AGENT_IN_FLIGHT.get(cache_key) is future:
                del _STRUCTURED_AGENT_IN_FLIGHT[cache_key]


def _try_structured_agent(extractor, documents: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, str | None]:
    try:
        return extractor(documents), None
    except LLMConfigurationError:
        raise
    except LLMGenerationError as error:
        logger.warning(
            "Structured extraction failed validation; using source-quoted deterministic findings: %s",
            error,
        )
        return None, str(error)


def _verified_items(
    documents: list[dict[str, Any]], items: Any,
) -> list[tuple[dict[str, Any], EvidenceReference]]:
    verified: list[tuple[dict[str, Any], EvidenceReference]] = []
    if not isinstance(items, list):
        return verified
    seen: set[tuple[str, str]] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        quote = _normalize_text(str(item.get("quote") or ""))
        evidence = _agent_evidence(documents, str(item.get("source") or ""), quote)
        description = _normalize_text(str(item.get("description") or item.get("name") or item.get("task") or quote))
        if evidence is None or len(description) < 8:
            continue
        quote_words = set(re.findall(r"[a-z0-9]+", quote.casefold()))
        description_words = set(re.findall(r"[a-z0-9]+", description.casefold())) - {"the", "a", "an", "and", "to", "for", "of", "in", "on", "is", "are", "may", "will", "must"}
        if description_words and len(description_words & quote_words) / len(description_words) < 0.3:
            continue
        for key in ("name", "owner", "assignee", "date", "due_date", "status", "priority", "impact", "likelihood", "severity"):
            value = item.get(key)
            if value and str(value).casefold() not in evidence.excerpt.casefold():
                item[key] = None
        if item.get("level") and not re.search(
            rf"(?:risk\s+)?severity\s*[:=]\s*{re.escape(str(item['level']))}\b|risk\s+level\s*[:=]\s*{re.escape(str(item['level']))}\b",
            evidence.excerpt, flags=re.IGNORECASE,
        ):
            item["level"] = None
        key = (str(item.get("category") or "").casefold(), description.casefold())
        if key in seen:
            continue
        seen.add(key)
        verified.append(({**item, "description": description}, evidence))
    return verified


def _verified_scope_claim(
    documents: list[dict[str, Any]],
    value: Any,
    source: Any,
    quote: Any,
) -> tuple[str, EvidenceReference] | None:
    if not isinstance(value, str) or not isinstance(source, str) or not isinstance(quote, str):
        return None
    verified = _verified_items(documents, [{
        "description": value,
        "source": source,
        "quote": quote,
    }])
    return (verified[0][0]["description"], verified[0][1]) if verified else None


def _source_main_project_goal(findings: list[ScopeFinding]) -> MainProjectGoal | None:
    candidates: list[tuple[int, str, EvidenceReference]] = []
    for finding in findings:
        if finding.category != "Goal" or not finding.evidence:
            continue
        statement = _normalize_text(finding.description)
        if len(statement) < 22 or statement.casefold() in {"meeting purpose", "project goal", "main project goal"}:
            continue
        evidence = finding.evidence[0]
        text = f"{statement} {evidence.excerpt}".casefold()
        score = 0
        if re.search(r"\b(?:(?:overall|main)\s+project\s+|(?:overall|main|project|pilot)\s+)goal\b|\bgoal\s*:", text):
            score += 4
        if re.search(r"\b(?:create|develop|build|provide|deliver|implement|establish|automate|combine)\b", statement, re.I):
            score += 2
        if re.search(r"\b(?:portal|platform|system|service|solution)\b", statement, re.I):
            score += 2
        if re.search(r"\b(?:approval|sign[- ]off|meeting purpose)\b", statement, re.I):
            score -= 3
        if 40 <= len(statement) <= 180:
            score += 1
        candidates.append((score, statement, evidence))
    if not candidates:
        return None
    _, statement, evidence = max(candidates, key=lambda item: (item[0], len(item[1])))
    problem = next((item for item in findings if item.category == "Problem" and item.evidence), None)
    outcome = next((item for item in findings if item.category == "Outcome" and item.evidence), None)
    return MainProjectGoal(
        statement=re.sub(
            r"^(?:(?:main|overall)\s+project\s+|(?:main|overall|project|pilot)\s+)?goal\s*[:\-]\s*",
            "",
            statement,
            flags=re.I,
        ),
        problem=problem.description if problem else None,
        expected_outcome=outcome.description if outcome else None,
        evidence=[evidence],
        problem_evidence=problem.evidence if problem else [],
        outcome_evidence=outcome.evidence if outcome else [],
    )


def _objective_title(description: str) -> str:
    normalized = _normalize_text(description)
    if ":" in normalized:
        prefix = normalized.split(":", 1)[0].strip()
        if 2 <= len(prefix.split()) <= 8:
            return prefix
    phrase = re.split(r"[.;]", normalized, maxsplit=1)[0].strip()
    words = phrase.split()
    return " ".join(words[:9]) + ("…" if len(words) > 9 else "")


def _supporting_objectives(
    objectives: list[str],
    findings: list[ScopeFinding],
    deliverables: list[str],
    main_goal: MainProjectGoal | None,
) -> list[SupportingObjective]:
    goal_words = set(re.findall(r"[a-z0-9]{3,}", (main_goal.statement if main_goal else "").casefold()))
    seen: set[str] = set()
    result: list[SupportingObjective] = []
    for description in objectives:
        normalized = _normalize_text(description)
        key = normalized.casefold()
        if not normalized or key in seen:
            continue
        words = set(re.findall(r"[a-z0-9]{3,}", key))
        overlap = len(words & goal_words) / max(1, len(words | goal_words))
        if overlap >= 0.82:
            continue
        finding = next((
            item for item in findings
            if item.category in {"Objective", "Requirement"}
            and _normalize_text(item.description).casefold() == key
            and item.evidence
        ), None)
        if finding is None:
            continue
        evidence_text = finding.evidence[0].excerpt.casefold()
        related_deliverable = None
        for deliverable in deliverables:
            name = _normalize_text(deliverable.split(";", 1)[0])
            if len(name) >= 8 and (name.casefold() in key or name.casefold() in evidence_text):
                related_deliverable = name
                break
        seen.add(key)
        result.append(SupportingObjective(
            id=f"objective-{len(result)+1}",
            title=_objective_title(normalized),
            description=normalized,
            contribution=f"Contributes to the main goal by accomplishing this documented objective: {normalized}",
            related_deliverable=related_deliverable,
            evidence=finding.evidence,
        ))
    return result


def _llm_scope_analysis(documents: list[dict[str, Any]]) -> dict[str, Any] | None:
    shape = '''{"main_project_goal":{"statement":"one overall project goal","source":"...","quote":"...","problem":"... or null","problem_source":"...","problem_quote":"...","expected_outcome":"... or null","outcome_source":"...","outcome_quote":"..."},"goals":[{"description":"...","source":"...","quote":"..."}],"objectives":[{"description":"one distinct supporting objective","source":"...","quote":"..."}],"scope":[{"description":"...","source":"...","quote":"..."}],"deliverables":[{"name":"...","description":"...","source":"...","quote":"..."}],"milestones":[{"name":"...","description":"...","date":null,"status":null,"owner":null,"source":"...","quote":"..."}],"responsibilities":[{"task":"...","owner":null,"role":null,"status":null,"source":"...","quote":"..."}],"dependencies":[{"description":"...","source":"...","quote":"..."}]}'''
    result = _run_structured_agent("Scope and Deliverable Extraction Agent", documents, shape)
    if result is None:
        return None
    groups: dict[str, list[tuple[dict[str, Any], EvidenceReference]]] = {
        key: _verified_items(documents, result.get(key))
        for key in ("goals", "objectives", "scope", "deliverables", "milestones", "responsibilities", "dependencies")
    }
    if any(isinstance(result.get(key), list) and result[key] for key in groups) and not any(groups.values()):
        raise LLMGenerationError("Scope extraction returned findings without verifiable source quotations.")
    goals = [_normalize_text(item["description"]) for item, _ in groups["goals"]]
    objectives = [_normalize_text(item["description"]) for item, _ in groups["objectives"]]
    scope = [_normalize_text(item["description"]) for item, _ in groups["scope"]]
    deliverables = [
        "; ".join(filter(None, [
            item.get("name"), item.get("description"),
            f"Due {item['date']}" if item.get("date") else None,
            f"Owner: {item['owner']}" if item.get("owner") else None,
            f"Status: {item['status']}" if item.get("status") else None,
        ])) for item, _ in groups["deliverables"]
    ]
    milestones = [ScopeMilestone(
        name=item.get("name") or item.get("description"), date=item.get("date"), status=item.get("status"),
        description=item.get("description") or item.get("name"), owner=item.get("owner"),
        source=evidence.filename, evidence=[evidence],
    ) for item, evidence in groups["milestones"]]
    responsibilities = [ScopeResponsibility(
        task=item.get("task") or item.get("description"), owner=item.get("owner"), role=item.get("role"),
        status=item.get("status"), source=evidence.filename, evidence=[evidence],
    ) for item, evidence in groups["responsibilities"]]
    dependencies = [ScopeDependency(
        from_task=item.get("from_task"), to_task=item.get("to_task"), description=item.get("description"),
        source=evidence.filename, evidence=[evidence],
    ) for item, evidence in groups["dependencies"]]
    finding_groups = [("Goal", groups["goals"]), ("Objective", groups["objectives"]), ("Scope", groups["scope"]), ("Deliverable", groups["deliverables"]), ("Milestone", groups["milestones"]), ("Responsibility", groups["responsibilities"]), ("Dependency", groups["dependencies"])]
    findings = []
    for category, entries in finding_groups:
        for item, evidence in entries:
            title = str(item.get("name") or item.get("task") or item.get("description") or category)
            description = str(item.get("description") or item.get("task") or item.get("name") or "")
            if category == "Deliverable":
                description = "; ".join(filter(None, [description, f"Due {item['date']}" if item.get("date") else None, f"Owner: {item['owner']}" if item.get("owner") else None, f"Status: {item['status']}" if item.get("status") else None]))
            findings.append(ScopeFinding(id=f"scope-{len(findings)+1}", category=category, title=title, description=description, evidence=[evidence]))
    raw_goal = result.get("main_project_goal")
    if isinstance(raw_goal, dict):
        verified_goal = _verified_scope_claim(
            documents, raw_goal.get("statement"), raw_goal.get("source"), raw_goal.get("quote"),
        )
    else:
        verified_goal = None
    main_project_goal = None
    if verified_goal:
        statement, evidence = verified_goal
        verified_problem = _verified_scope_claim(
            documents, raw_goal.get("problem"), raw_goal.get("problem_source"), raw_goal.get("problem_quote"),
        )
        verified_outcome = _verified_scope_claim(
            documents, raw_goal.get("expected_outcome"), raw_goal.get("outcome_source"), raw_goal.get("outcome_quote"),
        )
        main_project_goal = MainProjectGoal(
            statement=statement,
            problem=verified_problem[0] if verified_problem else None,
            expected_outcome=verified_outcome[0] if verified_outcome else None,
            evidence=[evidence],
            problem_evidence=[verified_problem[1]] if verified_problem else [],
            outcome_evidence=[verified_outcome[1]] if verified_outcome else [],
        )
    objective_text = [_normalize_text(item["description"]) for item, _ in groups["objectives"]]
    supporting_objectives = _supporting_objectives(
        objective_text, findings, deliverables, main_project_goal,
    )
    used = {e.filename for entries in groups.values() for _, e in entries if e.filename}
    if main_project_goal:
        used.update(
            evidence.filename
            for references in (
                main_project_goal.evidence,
                main_project_goal.problem_evidence,
                main_project_goal.outcome_evidence,
            )
            for evidence in references
            if evidence.filename
        )
    return ScopeAnalysis(
        goals=goals, objectives=objectives, scope=scope, deliverables=deliverables,
        main_project_goal=main_project_goal, supporting_objectives=supporting_objectives,
        milestones=milestones, responsibilities=responsibilities, dependencies=dependencies,
        findings=findings, source_documents=sorted(used),
        missing_information=[
            label for key, label in (("goals", "Project goals"), ("deliverables", "Project deliverables"), ("milestones", "Project milestones"), ("responsibilities", "Team responsibilities")) if not groups[key]
        ] + (["Main project goal needs clarification; the documents do not provide a verifiable overall-goal statement."] if main_project_goal is None else []),
    notes=[],
    ).model_dump(mode="json")


def _supported_relation(value: Any, evidence: EvidenceReference) -> str | None:
    if not isinstance(value, str):
        return None
    phrase = _normalize_text(value)
    return phrase if phrase and phrase.casefold() in evidence.excerpt.casefold() else None


def _supported_objective_relations(value: Any, evidence: EvidenceReference) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for candidate in value:
        phrase = _supported_relation(candidate, evidence)
        if phrase and phrase.casefold() not in {item.casefold() for item in result}:
            result.append(phrase)
    return result


def _llm_risk_analysis(documents: list[dict[str, Any]]) -> dict[str, Any] | None:
    shape = '''{"findings":[{"title":"...","category":"...","level":null,"description":"...","impact":null,"likelihood":null,"owner":null,"affected_milestones":[],"related_project_goal":null,"affected_objectives":[],"recommendations":[],"forecast":null,"source":"...","quote":"..."}]}'''
    result = _run_structured_agent("Risk Detection and Delivery Forecasting Agent", documents, shape)
    if result is None:
        return None
    findings = []
    for item, evidence in _verified_items(documents, result.get("findings")):
        level = normalize_risk_level(item.get("level"))
        findings.append(RiskFinding(
            id=f"risk-{len(findings)+1}", title=str(item.get("title") or "Project risk"), level=level,
            description=item["description"], impact=item.get("impact"), category=item.get("category"),
            likelihood=item.get("likelihood"), owner=item.get("owner"),
            affected_milestones=[str(v) for v in item.get("affected_milestones", []) if isinstance(v, str)],
            related_project_goal=_supported_relation(item.get("related_project_goal"), evidence),
            affected_objectives=_supported_objective_relations(item.get("affected_objectives"), evidence),
            evidence=[evidence], recommendations=[str(v) for v in item.get("recommendations", []) if isinstance(v, str)],
            forecast=item.get("forecast"), inference=False,
        ))
    if isinstance(result.get("findings"), list) and result["findings"] and not findings:
        raise LLMGenerationError("Risk extraction returned findings without verifiable source quotations.")
    used = sorted({f.evidence[0].filename for f in findings if f.evidence})
    return RiskAnalysis(findings=findings, source_documents=used,
        missing_information=[] if findings else ["No relevant information was found in the uploaded documents."],
        rules_used=[
            "LLM findings require verified source quotations.",
            "Severity uses source-stated or explainable source-backed probability x impact; insufficient dimensions remain unrated.",
        ]
    ).model_dump(mode="json")


def _llm_blocker_analysis(documents: list[dict[str, Any]]) -> dict[str, Any] | None:
    item_shape = '{"description":"...","status":null,"assignee":null,"due_date":null,"priority":null,"related_task":null,"related_milestone":null,"related_project_goal":null,"affected_objectives":[],"source":"...","quote":"..."}'
    shape = (
        '{"blockers":[' + item_shape + '],"unresolved_issues":[' + item_shape
        + '],"pending_decisions":[' + item_shape + '],"action_items":[' + item_shape + ']}'
    )
    result = _run_structured_agent("Blocker and Action Item Identification Agent", documents, shape)
    if result is None:
        return None
    all_findings: dict[str, list[BlockerItem]] = {}
    for key in ("blockers", "unresolved_issues", "pending_decisions", "action_items"):
        verified = _verified_items(documents, result.get(key))
        if key == "blockers":
            verified = [
                (item, evidence) for item, evidence in verified
                if re.search(
                    r"\b(?:blocker|blocked|blocks|waiting for|waiting on|awaiting|cannot proceed|"
                    r"requires approval|approval required)\b",
                    f"{item.get('description', '')} {item.get('quote', '')}",
                    flags=re.IGNORECASE,
                )
            ]
        all_findings[key] = [BlockerItem(
            id=f"{key}-{index+1}", description=item["description"], category=key,
            status=str(item["status"]) if item.get("status") else None,
            assignee=item.get("assignee") or item.get("owner"), due_date=item.get("due_date"),
            priority=item.get("priority"), related_task=item.get("related_task"),
            related_milestone=item.get("related_milestone"),
            related_project_goal=_supported_relation(item.get("related_project_goal"), evidence),
            affected_objectives=_supported_objective_relations(item.get("affected_objectives"), evidence),
            source_document=evidence.filename,
            evidence=[evidence],
        ) for index, (item, evidence) in enumerate(verified)]
    if any(isinstance(result.get(key), list) and result[key] for key in all_findings) and not any(all_findings.values()):
        raise LLMGenerationError("Blocker extraction returned findings without verifiable source quotations.")
    used = sorted({item.source_document for group in all_findings.values() for item in group if item.source_document})
    found = any(all_findings.values())
    return BlockerAnalysis(
        blockers=all_findings.get("blockers", []), unresolved_issues=all_findings.get("unresolved_issues", []),
        pending_decisions=all_findings.get("pending_decisions", []),
        action_items=all_findings.get("action_items", []), source_documents=used,
        missing_information=[] if found else ["No relevant information was found in the uploaded documents."],
        notes=[],
    ).model_dump(mode="json")


def _merge_text_values(primary: list[str], secondary: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for value in [*primary, *secondary]:
        normalized = _normalize_text(value)
        key = normalized.casefold()
        if normalized and key not in seen:
            seen.add(key)
            merged.append(normalized)
    return merged


def _merge_scope_results(source_result: dict[str, Any], model_result: dict[str, Any]) -> dict[str, Any]:
    source = ScopeAnalysis.model_validate(source_result)
    model = ScopeAnalysis.model_validate(model_result)
    deliverable_names = {
        _normalize_text(value.split(";", 1)[0]).casefold()
        for value in [*source.deliverables, *model.deliverables]
        if _normalize_text(value)
    }

    def is_deliverable_label(value: str) -> bool:
        normalized = _normalize_text(value).casefold()
        if not normalized:
            return False
        words = set(re.findall(r"[a-z0-9]+", normalized))
        return any(
            normalized == name or (len(words) <= 5 and words <= set(re.findall(r"[a-z0-9]+", name)))
            for name in deliverable_names
        )

    model.goals = [item for item in model.goals if not is_deliverable_label(item)]
    model.objectives = [item for item in model.objectives if not is_deliverable_label(item)]
    model.scope = [item for item in model.scope if not is_deliverable_label(item)]
    findings = list(source.findings)
    seen = {_normalize_text(item.description).casefold() for item in findings}
    for item in model.findings:
        if item.category in {"Goal", "Objective", "Scope"} and is_deliverable_label(item.description):
            continue
        key = _normalize_text(item.description).casefold()
        if key and key not in seen:
            findings.append(item)
            seen.add(key)
    findings = [item.model_copy(update={"id": f"scope-{index}"}) for index, item in enumerate(findings, start=1)]
    main_project_goal = model.main_project_goal or source.main_project_goal
    objectives = _merge_text_values(source.objectives, model.objectives)
    supporting_objectives = _supporting_objectives(
        objectives,
        findings,
        _merge_text_values(source.deliverables, model.deliverables),
        main_project_goal,
    )
    missing_information = [
        item for item in source.missing_information
        if "project goal" not in item.casefold()
    ]
    if main_project_goal is None:
        missing_information.append(
            "Main project goal needs clarification; the documents do not provide a verifiable overall-goal statement."
        )
    result = ScopeAnalysis(
        goals=_merge_text_values(source.goals, model.goals),
        objectives=objectives,
        main_project_goal=main_project_goal,
        supporting_objectives=supporting_objectives,
        scope=_merge_text_values(source.scope, model.scope),
        deliverables=_merge_text_values(source.deliverables, model.deliverables),
        milestones=source.milestones + [item for item in model.milestones if not any(
            _normalize_text(item.name).casefold() == _normalize_text(existing.name).casefold()
            and item.date == existing.date for existing in source.milestones
        )],
        project_start_date=source.project_start_date or model.project_start_date,
        project_end_date=source.project_end_date or model.project_end_date,
        responsibilities=source.responsibilities + [item for item in model.responsibilities if not any(
            _normalize_text(item.task).casefold() == _normalize_text(existing.task).casefold()
            and _normalize_text(item.owner).casefold() == _normalize_text(existing.owner).casefold()
            for existing in source.responsibilities
        )],
        dependencies=source.dependencies + [item for item in model.dependencies if not any(
            _normalize_text(item.description).casefold() == _normalize_text(existing.description).casefold()
            for existing in source.dependencies
        )],
        progress=_merge_text_values(source.progress, model.progress),
        findings=findings,
        missing_information=missing_information,
        source_documents=sorted(set(source.source_documents) | set(model.source_documents)),
        notes=[],
    )
    return result.model_dump(mode="json")


def _merge_risk_results(source_result: dict[str, Any], model_result: dict[str, Any]) -> dict[str, Any]:
    source = RiskAnalysis.model_validate(source_result)
    model = RiskAnalysis.model_validate(model_result)
    findings = _dedupe_risk_findings([*source.findings, *model.findings])
    for index, finding in enumerate(findings, start=1):
        finding.id = f"risk-{index}"
    return RiskAnalysis(
        findings=findings,
        missing_information=[] if findings else _merge_text_values(source.missing_information, model.missing_information),
        source_documents=sorted(set(source.source_documents) | set(model.source_documents)),
        rules_used=_merge_text_values(source.rules_used, model.rules_used),
        notes=[],
    ).model_dump(mode="json")


def _merge_blocker_results(source_result: dict[str, Any], model_result: dict[str, Any]) -> dict[str, Any]:
    source = BlockerAnalysis.model_validate(source_result)
    model = BlockerAnalysis.model_validate(model_result)
    groups: dict[str, list[BlockerItem]] = {}
    seen_descriptions: set[str] = set()
    for key in ("blockers", "unresolved_issues", "pending_decisions", "action_items"):
        merged = _dedupe_blocker_items([*getattr(source, key), *getattr(model, key)])
        unique = []
        for item in merged:
            normalized = _normalize_text(item.description).casefold()
            if not normalized or normalized in seen_descriptions:
                continue
            seen_descriptions.add(normalized)
            item.id = f"{key}-{len(unique) + 1}"
            unique.append(item)
        groups[key] = unique
    used = sorted(set(source.source_documents) | set(model.source_documents))
    any_findings = any(groups.values())
    return BlockerAnalysis(
        **groups,
        missing_information=[] if any_findings else _merge_text_values(source.missing_information, model.missing_information),
        source_documents=used,
        notes=[],
    ).model_dump(mode="json")


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
    use_llm: bool = True,
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

    structured, llm_error = _try_structured_agent(_llm_scope_analysis, _analysis_documents(documents)) if use_llm else (None, None)
    if structured is not None:
        source_result = analyze_scope(
            document_ids=[document["id"] for document in documents], persist=False, use_llm=False,
        )
        structured = _merge_scope_results(source_result, structured)
        if persist:
            _persist_analysis("scope", [document["id"] for document in documents], structured)
        return structured

    documents = _analysis_documents(documents)
    goals: list[str] = []
    objectives: list[str] = []
    scope: list[str] = []
    deliverables: list[str] = []
    milestones: list[ScopeMilestone] = []
    responsibilities: list[ScopeResponsibility] = []
    dependencies: list[ScopeDependency] = []
    progress: list[str] = []
    findings: list[ScopeFinding] = []
    missing_information: list[str] = []
    source_documents: list[str] = []
    notes: list[str] = ["Some findings were extracted directly from uploaded document evidence."] if llm_error else []
    project_start_date: str | None = None
    project_end_date: str | None = None

    for document in documents:
        source_documents.append(document.get("filename") or document.get("id") or "unknown")
        text = document.get("extracted_text") or ""
        sentences = _sentence_split(text)
        chunks = _load_chunks_for_document(document["id"])
        register_blocks = _structured_register_blocks(document)
        for label, category, target in (
            ({"goal", "project goal", "main project goal", "overall goal", "purpose", "project purpose"}, "Goal", goals),
            ({"success criteria", "objective", "project objective"}, "Objective", objectives),
            ({"out of scope", "scope boundary"}, "Scope", scope),
        ):
            for value in _following_labeled_lines(document, label):
                reference = _build_reference(document, value, chunks)
                if reference.excerpt and value not in target:
                    target.append(value)
                    findings.append(ScopeFinding(
                        id=f"scope-{len(findings)+1}", category=category,
                        title=category, description=value, evidence=[reference],
                    ))
        for labels, category in (
            ({"problem", "main problem", "problem statement"}, "Problem"),
            ({"expected outcome", "project outcome", "target outcome"}, "Outcome"),
        ):
            for value in _following_labeled_lines(document, labels):
                reference = _build_reference(document, value, chunks)
                if reference.excerpt:
                    findings.append(ScopeFinding(
                        id=f"scope-{len(findings)+1}", category=category,
                        title=category, description=value, evidence=[reference],
                    ))
        duration_match = re.search(
            r"(?:duration|planned duration)[^\n]*\n?\s*[^\n]*?(\d{1,2}\s+[A-Za-z]+\s+\d{4})\s+(?:to|through|–|-)\s+(\d{1,2}\s+[A-Za-z]+\s+\d{4})",
            text, flags=re.IGNORECASE,
        )
        if duration_match:
            project_start_date, project_end_date = duration_match.groups()
        for block in register_blocks:
            if block.get("worksheet", "").casefold() == "requirements" and block.get("requirement"):
                requirement = block["requirement"]
                objectives.append(requirement)
                scope.append(requirement)
                evidence = _build_reference(document, block["_excerpt"], chunks)
                if evidence.excerpt:
                    findings.append(ScopeFinding(
                        id=f"scope-{len(findings)+1}", category="Requirement",
                        title=block.get("area") or "Project requirement", description=requirement,
                        evidence=[evidence],
                    ))
        seen_responsibilities: set[tuple[str, str]] = set()
        for block in register_blocks:
            task = block.get("deliverable")
            owner = block.get("owner")
            if not task or not owner:
                continue
            reference = _build_reference(document, block["_excerpt"], chunks)
            key = (task.casefold(), owner.casefold())
            if reference.excerpt and key not in seen_responsibilities:
                seen_responsibilities.add(key)
                responsibilities.append(ScopeResponsibility(
                    task=task, owner=owner, role="deliverable owner", status=block.get("status"),
                    source=document.get("filename"), evidence=[reference],
                ))
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
            if not lower or _is_structural_heading(normalized):
                continue
            problem_match = re.match(r"^(?:main\s+)?problem(?:\s+statement)?\s*[:\-]\s*(.+)$", normalized, re.I)
            outcome_match = re.match(r"^(?:expected\s+outcome|project\s+outcome|target\s+outcome)\s*[:\-]\s*(.+)$", normalized, re.I)
            if problem_match or outcome_match:
                claim = problem_match or outcome_match
                category = "Problem" if problem_match else "Outcome"
                findings.append(ScopeFinding(
                    id=f"scope-{len(findings)+1}", category=category, title=category,
                    description=claim.group(1).strip(), evidence=[reference] if reference.excerpt else [],
                ))
            if re.match(r"^deliverable\s*:", lower) and any(block.get("deliverable") for block in register_blocks):
                continue
            if "objective" in lower:
                objective = re.sub(
                    r"^(?:supporting\s+)?(?:project\s+)?objective(?:\s+\d+)?\s*[:\-]\s*",
                    "", normalized, flags=re.I,
                ).strip()
                if objective and objective not in objectives:
                    objectives.append(objective)
                    findings.append(ScopeFinding(
                        id=f"scope-{len(findings)+1}",
                        category="Objective",
                        title="Project objective",
                        description=objective,
                        evidence=[reference] if reference.excerpt else [],
                    ))
            elif any(keyword in lower for keyword in ["goal", "purpose", "aim", "mission", "target outcome"]):
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
            if any(keyword in lower for keyword in ["owner:", "responsible for", "assigned to", "team member:"]):
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
            if re.match(r"^(?:progress|project status|status)\s*:", lower):
                progress.append(normalized)
                findings.append(ScopeFinding(
                    id=f"scope-{len(findings) + 1}",
                    category="Progress",
                    title=normalized.split(":", 1)[0].strip().title(),
                    description=normalized,
                    evidence=[reference] if reference.excerpt else [],
                ))

    if not goals: missing_information.append("Project goals were not explicitly stated in the indexed documents.")
    if not deliverables: missing_information.append("No explicit deliverables or outcomes were identified.")
    if not responsibilities:
        missing_information.append("No explicit responsibilities were identified in the documents.")
    if not dependencies:
        missing_information.append("No explicit task dependencies were identified in the sourced documents.")
    if not progress:
        missing_information.append("No progress or status statements were found for the project timeline.")

    structured_deliverables: list[str] = []
    for document in documents:
        chunks = _load_chunks_for_document(document["id"])
        for block in _structured_register_blocks(document):
            name = block.get("deliverable")
            if not name:
                continue
            details = "; ".join(filter(None, [
                f"Due {block['due date']}" if block.get("due date") else None,
                f"Status: {block['status']}" if block.get("status") else None,
                f"Owner: {block['owner']}" if block.get("owner") else None,
            ]))
            value = f"{name}; {details}" if details else name
            if value not in structured_deliverables:
                structured_deliverables.append(value)
                reference = _build_reference(document, block["_excerpt"], chunks)
                if reference.excerpt:
                    findings.append(ScopeFinding(
                        id=f"scope-{len(findings)+1}", category="Deliverable", title=name,
                        description=value, related_deliverable=name, evidence=[reference],
                    ))
    if structured_deliverables:
        structured_names = [item.split(";", 1)[0].casefold() for item in structured_deliverables]
        deliverables = structured_deliverables + [
            item for item in deliverables
            if not any(name in item.casefold() for name in structured_names)
        ]

    def unique_text(items: list[str]) -> list[str]:
        seen: set[str] = set()
        return [item for item in items if (key := _normalize_text(item).casefold()) and not (key in seen or seen.add(key))]

    goals = unique_text(goals)
    objectives = unique_text(objectives)
    scope = unique_text(scope)
    deliverables = unique_text(deliverables)
    seen_finding_descriptions: set[str] = set()
    unique_findings = []
    for finding in findings:
        key = _normalize_text(finding.description).casefold()
        if key and key not in seen_finding_descriptions:
            seen_finding_descriptions.add(key)
            unique_findings.append(finding)
    findings = [finding.model_copy(update={"id": f"scope-{index}"}) for index, finding in enumerate(unique_findings, start=1)]
    main_project_goal = _source_main_project_goal(findings)
    supporting_objectives = _supporting_objectives(
        objectives, findings, deliverables, main_project_goal,
    )
    if main_project_goal is None:
        missing_information.append(
            "Main project goal needs clarification; the documents do not provide a verifiable overall-goal statement."
        )

    payload = ScopeAnalysis(
        goals=goals[:25],
        objectives=objectives[:25],
        main_project_goal=main_project_goal,
        supporting_objectives=supporting_objectives,
        scope=scope[:50],
        deliverables=deliverables[:25],
        milestones=milestones[:25],
        responsibilities=responsibilities[:25],
        dependencies=dependencies[:25],
        progress=progress[:25],
        findings=findings[:100],
        missing_information=missing_information,
        source_documents=sorted(set(source_documents)),
        project_start_date=project_start_date,
        project_end_date=project_end_date,
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
    use_llm: bool = True,
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

    structured, llm_error = _try_structured_agent(_llm_risk_analysis, _analysis_documents(documents)) if use_llm else (None, None)
    if structured is not None:
        source_result = analyze_risks(
            document_ids=[document["id"] for document in documents], persist=False, use_llm=False,
        )
        structured = _merge_risk_results(source_result, structured)
        if persist:
            _persist_analysis("risk", [document["id"] for document in documents], structured)
        return structured

    documents = _analysis_documents(documents)
    findings: list[RiskFinding] = []
    missing_information: list[str] = []
    source_documents: list[str] = []
    rules_used = [
        "Findings are linked to documented risks, dependencies, milestones, and delivery status.",
        "Severity uses source-stated or explainable source-backed probability x impact; insufficient dimensions remain unrated.",
    ]
    if llm_error:
        rules_used.append("Some findings were extracted directly from uploaded document evidence.")

    for document in documents:
        source_documents.append(document.get("filename") or document.get("id") or "unknown")
        text = document.get("extracted_text") or ""
        sentences = _sentence_split(text)
        chunks = _load_chunks_for_document(document["id"])
        for block in _structured_register_blocks(document):
            description = block.get("risk description")
            if not description:
                continue
            evidence = _build_reference(document, block["_excerpt"], chunks)
            if not evidence.excerpt:
                continue
            details = [description]
            if block.get("impact"):
                details.append(f"Impact: {block['impact']}")
            if block.get("likelihood"):
                details.append(f"Likelihood: {block['likelihood']}")
            if block.get("owner"):
                details.append(f"Owner: {block['owner']}")
            if block.get("mitigation"):
                details.append(f"Mitigation: {block['mitigation']}")
            findings.append(RiskFinding(
                id=f"risk-{len(findings)+1}", title=description, level=None,
                category="Risk register", description=". ".join(details), impact=block.get("impact"),
                likelihood=block.get("likelihood"), owner=block.get("owner"),
                evidence=[evidence], recommendations=[block["mitigation"]] if block.get("mitigation") else [],
                inference=False,
            ))
            continue
        for block in _structured_register_blocks(document):
            if block.get("worksheet", "").casefold() != "dependencies":
                continue
            status = block.get("status", "").casefold()
            if status not in {"pending", "not confirmed", "pending decision", "blocked"}:
                continue
            dependency = block.get("dependency")
            required = block.get("required input")
            if not dependency or not required:
                continue
            evidence = _build_reference(document, block["_excerpt"], chunks)
            details = [f"{dependency} depends on {required}"]
            if block.get("impact"):
                details.append(f"Impact: {block['impact']}")
            details.append(f"Status: {block['status']}")
            if block.get("owner"):
                details.append(f"Owner: {block['owner']}")
            if block.get("due date"):
                details.append(f"Due {block['due date']}")
            if evidence.excerpt:
                findings.append(RiskFinding(
                    id=f"risk-{len(findings)+1}", title=f"Dependency gap: {dependency}",
                    level=None, category="Dependency gap", description="; ".join(details),
                    impact=block.get("impact"), likelihood=block.get("likelihood"), owner=block.get("owner"),
                    evidence=[evidence], inference=False,
                ))
        for sentence in sentences:
            normalized = sentence.strip()
            lower = normalized.lower()
            if _is_structural_heading(normalized) or re.match(r"^(?:status|impact|likelihood|owner|mitigation|risk id|risk description)\s*:", lower):
                continue
            if len(re.findall(r"[A-Za-z0-9]+", normalized)) < 4:
                continue
            if not re.search(r"\brisk\b", lower) and not any(keyword in lower for keyword in ["delayed", "overdue", "blocked", "behind schedule", "cannot begin", "cannot be tested", "manual cleanup"]):
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

            if lower.startswith("risk description:"):
                continue
            if any(_normalize_text(item.description).casefold() in _normalize_text(normalized).casefold() for item in findings):
                continue
            severity_match = re.search(r"(?:severity|priority)\s*:\s*(low|medium|high|critical)", lower)
            level = severity_match.group(1).title() if severity_match else None
            recommendations: list[str] = []
            forecast = None

            findings.append(
                RiskFinding(
                    id=f"risk-{len(findings) + 1}",
                    title=title,
                    level=level,
                    description=normalized,
                    category="Schedule or delivery risk",
                    owner=(re.search(r"\bowner\s*:\s*([^.;]+)", normalized, re.IGNORECASE).group(1).strip()
                           if re.search(r"\bowner\s*:\s*([^.;]+)", normalized, re.IGNORECASE) else None),
                    likelihood=(re.search(r"\blikelihood\s*:\s*([^.;]+)", normalized, re.IGNORECASE).group(1).strip()
                                if re.search(r"\blikelihood\s*:\s*([^.;]+)", normalized, re.IGNORECASE) else None),
                    affected_milestones=[segment for segment in _find_dates(normalized) if segment],
                    evidence=[reference] if reference.excerpt else [],
                    recommendations=recommendations,
                    forecast=forecast,
                    inference=False,
                )
            )

    findings = _dedupe_risk_findings(findings)
    if not findings:
        missing_information.append("No risk evidence was found in the indexed project documents.")
    if not source_documents:
        missing_information.append("No source documents were associated with the analysis.")

    payload = RiskAnalysis(
        findings=findings[:25],
        missing_information=missing_information,
        source_documents=sorted(set(source_documents)),
        rules_used=rules_used,
        notes=["Some findings were extracted directly from uploaded document evidence."] if llm_error else [],
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
    use_llm: bool = True,
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

    structured, llm_error = _try_structured_agent(_llm_blocker_analysis, _analysis_documents(documents)) if use_llm else (None, None)
    if structured is not None:
        source_result = analyze_blockers(
            document_ids=[document["id"] for document in documents], persist=False, use_llm=False,
        )
        structured = _merge_blocker_results(source_result, structured)
        if persist:
            _persist_analysis("blockers", [document["id"] for document in documents], structured)
        return structured

    documents = _analysis_documents(documents)
    blockers: list[BlockerItem] = []
    unresolved_issues: list[BlockerItem] = []
    pending_decisions: list[BlockerItem] = []
    action_items: list[BlockerItem] = []
    missing_information: list[str] = []
    source_documents: list[str] = []
    notes: list[str] = ["Some findings were extracted directly from uploaded document evidence."] if llm_error else []

    for document in documents:
        source_documents.append(document.get("filename") or document.get("id") or "unknown")
        text = document.get("extracted_text") or ""
        sentences = _sentence_split(text)
        chunks = _load_chunks_for_document(document["id"])
        for block in _structured_register_blocks(document):
            worksheet = block.get("worksheet", "").casefold()
            evidence = _build_reference(document, block["_excerpt"], chunks)
            if worksheet == "action items" and block.get("action") and evidence.excerpt:
                action = BlockerItem(
                    id=f"action-{len(action_items)+1}", description=block["action"], category="action_item",
                    status=block.get("status"), assignee=block.get("owner"), due_date=block.get("due date"),
                    priority=block.get("priority"), source_document=document.get("filename"), evidence=[evidence],
                    recommendation=block.get("notes"),
                )
                is_decision_action = (block.get("status") or "").casefold() == "pending decision"
                if not is_decision_action:
                    action_items.append(action)
                if is_decision_action:
                    pending_decisions.append(BlockerItem(
                        id=f"decision-{len(pending_decisions)+1}", description=block["action"],
                        category="pending_decision", status=block["status"], assignee=block.get("owner"),
                        due_date=block.get("due date"), priority=block.get("priority"),
                        source_document=document.get("filename"), evidence=[evidence],
                    ))
            if worksheet == "dependencies" and block.get("dependency") and evidence.excerpt:
                status = block.get("status", "").casefold()
                if status in {"not confirmed", "pending", "blocked"}:
                    item = BlockerItem(
                        id=f"dependency-{len(unresolved_issues)+len(blockers)+1}",
                        description="; ".join(filter(None, [
                            f"{block['dependency']} requires {block.get('required input', '')}".strip(),
                            block.get("impact"), f"Status: {block.get('status')}",
                        ])),
                        category="dependency", status=block.get("status"), assignee=block.get("owner"),
                        due_date=block.get("due date"), related_task=block.get("dependency"),
                        source_document=document.get("filename"), evidence=[evidence],
                    )
                    if block.get("impact", "").casefold().startswith("blocks"):
                        blockers.append(item)
                    else:
                        unresolved_issues.append(item)
                elif status == "pending decision":
                    pending_decisions.append(BlockerItem(
                        id=f"decision-{len(pending_decisions)+1}",
                        description=f"Decision needed for {block['dependency']}: {block.get('required input', '')}",
                        category="pending_decision", status=block.get("status"), assignee=block.get("owner"),
                        due_date=block.get("due date"), related_task=block.get("dependency"),
                        source_document=document.get("filename"), evidence=[evidence],
                    ))
        for sentence in sentences:
            normalized = sentence.strip()
            lower = normalized.lower()
            if _is_structural_heading(normalized):
                continue
            is_pending_decision = any(keyword in lower for keyword in [
                "pending decision", "decision required",
                "must be approved", "approval needed", "requires approval",
                "must decide", "must choose", "need to decide", "needs to decide", "should decide",
                "decide which", "decide whether", "decide what", "decide who", "decide when",
                "needs a decision", "decision is pending",
            ]) or bool(re.match(r"^(?:action\s*:\s*)?(?:choose|select|decide)\b", lower))
            is_action_item = any(keyword in lower for keyword in ["action item", "follow up"]) or bool(
                re.match(r"^(?:\d+[.)]\s*)?action\s*:", normalized, flags=re.IGNORECASE)
            ) or bool(
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
            if lower.startswith("blocker or next step:"):
                detail = lower.split(":", 1)[1]
                is_action_item = False
                is_blocker = any(term in detail for term in ("blocked", "waiting", "pending", "approval required", "awaiting"))
                is_pending_decision = False
                if not is_blocker:
                    continue
            if not (is_blocker or is_pending_decision or is_action_item):
                continue
            reference = _build_reference(document, sentence, chunks)
            category = "pending_decision" if is_pending_decision else "action_item" if is_action_item else "issue"
            status = None
            if any(keyword in lower for keyword in ["completed", "complete", "resolved", "approved"]):
                status = "completed"
            elif "blocked" in lower or "cannot begin" in lower or "cannot proceed" in lower:
                status = "blocked"
            elif is_pending_decision or "pending" in lower or "awaiting" in lower:
                status = "pending"
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
                    r"^\s*(?:\d+[.)]\s*)?action\s*[:\-]\s*([A-Z][A-Za-z'-]*)\s*(?:\([^)]*\))?\s+(?:must|will|should)\b",
                    normalized,
                    flags=re.IGNORECASE,
                )
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
                recommendation=None,
            )

            if is_pending_decision:
                pending_decisions.append(payload_item)
            elif is_action_item:
                action_items.append(payload_item)
            else:
                blockers.append(payload_item)

    blockers = _dedupe_blocker_items(blockers)
    unresolved_issues = _dedupe_blocker_items(unresolved_issues)
    pending_decisions = _dedupe_blocker_items(pending_decisions)
    action_items = _dedupe_blocker_items(action_items)
    source_documents = sorted({
        item.source_document for group in (blockers, unresolved_issues, pending_decisions, action_items)
        for item in group if item.source_document
    })
    if not blockers and not unresolved_issues and not pending_decisions and not action_items:
        missing_information.append("No blocker, pending decision, or action item evidence was found in the indexed documents.")
    if not source_documents:
        missing_information.append("No source documents were provided for blocker analysis.")

    payload = BlockerAnalysis(
        blockers=blockers[:25],
        unresolved_issues=unresolved_issues[:25],
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


class LLMGenerationError(RuntimeError):
    pass


class LLMConfigurationError(LLMGenerationError):
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


def maybe_call_llm(prompt: str, context: str, *, structured: bool = False) -> str:
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
    logger.info(
        "LLM request started provider=%s model=%s structured=%s prompt_chars=%d context_chars=%d "
        "context_limit=%d output_limit=%d timeout_seconds=%.1f",
        provider,
        settings.openai_model if provider == "openai" else (
            settings.azure_openai_deployment if provider == "azure" else settings.ollama_model
        ),
        structured,
        len(prompt),
        len(context),
        4096 if provider == "ollama" else 0,
        256 if structured else 384,
        timeout_seconds,
    )
    try:
        if provider == "openai":
            client = _openai_client(settings.openai_api_key, timeout_seconds)
            response = client.responses.create(
                model=settings.openai_model,
                instructions=prompt,
                input=f"Complete extracted project document chunks:\n{context[:48000]}",
                max_output_tokens=256 if structured else 384,
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
                input=f"Complete extracted project document chunks:\n{context[:48000]}",
                max_output_tokens=256 if structured else 384,
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
            client = _ollama_http_client(settings.ollama_base_url, timeout_seconds)
            request_body = {
                    "model": settings.ollama_model,
                    "stream": False,
                    "keep_alive": "10m",
                    **({"format": "json"} if structured else {}),
                    "options": {
                        "temperature": 0,
                        "top_p": 0.65,
                        "num_ctx": 4096,
                        "num_predict": 512 if structured else 384,
                    },
                    "messages": [
                        {"role": "system", "content": prompt},
                        {
                            "role": "user",
                            "content": f"Complete extracted project document chunks:\n{context[:48000]}",
                        },
                    ],
                }
            response = None
            for attempt in range(2):
                try:
                    response = client.post("/api/chat", json=request_body)
                except (httpx.ConnectError, httpx.ConnectTimeout, httpx.RemoteProtocolError):
                    if attempt == 1:
                        raise
                    logger.warning("Ollama connection failed; retrying once after a short delay.")
                    sleep(0.35)
                    continue
                status_code = getattr(response, "status_code", 200)
                if status_code in {429, 500, 502, 503, 504} and attempt == 0:
                    logger.warning("Ollama returned transient HTTP %s; retrying once.", status_code)
                    sleep(0.35)
                    continue
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as error:
                    response_text = (
                        f"{getattr(error.response, 'text', '') or ''} {error}"
                    ).casefold()
                    if error.response.status_code == 404 and "model" in response_text:
                        raise LLMConfigurationError(
                            f"Ollama is reachable, but configured model '{settings.ollama_model}' "
                            "is unavailable. Verify it with `ollama list`."
                        ) from error
                    logger.error(
                        "Ollama request rejected status=%s response_excerpt=%r",
                        error.response.status_code,
                        (getattr(error.response, "text", "") or "")[:300],
                    )
                    raise LLMGenerationError(
                        f"Ollama rejected the request with HTTP {error.response.status_code}. "
                        "Check the Ollama service and model logs, then retry."
                    ) from error
                break
            if response is None:
                raise LLMGenerationError("Ollama did not return a response.")
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
                "LLM generation timed out provider=%s duration_seconds=%.1f timeout_seconds=%.1f "
                "structured=%s prompt_chars=%d context_chars=%d",
                provider,
                elapsed_seconds,
                timeout_seconds,
                structured,
                len(prompt),
                len(context),
            )
            raise LLMTimeoutError(
                f"The configured {provider} answer-generation service timed out after "
                f"{timeout_seconds:g} seconds. Retry the question."
            ) from error
        if provider == "ollama" and isinstance(error, httpx.ConnectError):
            logger.exception(
                "Could not connect to Ollama base_url=%s model=%s",
                settings.ollama_base_url,
                settings.ollama_model,
            )
            raise LLMGenerationError(
                "Could not connect to the configured Ollama service. Confirm Ollama is running "
                "at OLLAMA_BASE_URL and that the configured model is installed."
            ) from error
        if provider == "ollama" and isinstance(error, httpx.RemoteProtocolError):
            logger.exception("Ollama closed the connection before returning a response.")
            raise LLMGenerationError(
                "Ollama closed the answer-generation connection unexpectedly. Check the Ollama "
                "service logs and retry."
            ) from error
        logger.exception("Configured %s answer generation failed", provider)
        raise LLMGenerationError(
            f"The configured {provider} answer-generation service could not complete the request. "
            "Check provider access, model/deployment name, quota, and API logs, then retry."
        ) from error
