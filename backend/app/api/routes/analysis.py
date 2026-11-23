import logging
from time import perf_counter

from fastapi import APIRouter, Body, HTTPException

from app.schemas.analysis import AnalysisRequest
from app.services.project_agents import (
    LLMGenerationError, analyze_blockers, analyze_risks, analyze_scope,
)

router = APIRouter(prefix="/analysis", tags=["analysis"])
logger = logging.getLogger(__name__)


def _execute(agent, **kwargs) -> dict:
    started = perf_counter()
    agent_name = getattr(agent, "__name__", "project_analysis")
    try:
        result = agent(**kwargs)
    except LLMGenerationError as error:
        logger.exception("Analysis agent failed agent=%s duration_ms=%.1f", agent_name, (perf_counter() - started) * 1000)
        raise HTTPException(status_code=503, detail=f"AI analysis did not complete: {error}") from error
    logger.info(
        "Analysis agent completed agent=%s duration_ms=%.1f selected_documents=%d findings=%d",
        agent_name,
        (perf_counter() - started) * 1000,
        len(kwargs.get("document_ids") or []) + int(bool(kwargs.get("document_id"))),
        len(result.get("findings") or []) + len(result.get("blockers") or [])
        + len(result.get("unresolved_issues") or []) + len(result.get("pending_decisions") or [])
        + len(result.get("action_items") or []),
    )
    return result


@router.api_route("/scope", methods=["GET", "POST"])
def scope_analysis(
    document_id: str | None = None,
    document_ids: list[str] | None = None,
    query: str | None = None,
    top_k: int = 5,
    payload: AnalysisRequest | None = Body(default=None),
) -> dict:
    request = payload or AnalysisRequest(document_id=document_id, document_ids=document_ids, query=query, top_k=top_k)
    if request.document_id is None and not request.document_ids and not document_id and not document_ids:
        return _execute(analyze_scope, query=request.query, top_k=request.top_k)
    return _execute(analyze_scope,
        document_id=request.document_id or document_id,
        document_ids=request.document_ids or document_ids,
        query=request.query or query,
        top_k=request.top_k or top_k,
    )


@router.api_route("/risks", methods=["GET", "POST"])
def risk_analysis(
    document_id: str | None = None,
    document_ids: list[str] | None = None,
    query: str | None = None,
    top_k: int = 5,
    payload: AnalysisRequest | None = Body(default=None),
) -> dict:
    request = payload or AnalysisRequest(document_id=document_id, document_ids=document_ids, query=query, top_k=top_k)
    if request.document_id is None and not request.document_ids and not document_id and not document_ids:
        return _execute(analyze_risks, query=request.query, top_k=request.top_k)
    return _execute(analyze_risks,
        document_id=request.document_id or document_id,
        document_ids=request.document_ids or document_ids,
        query=request.query or query,
        top_k=request.top_k or top_k,
    )


@router.api_route("/blockers", methods=["GET", "POST"])
def blocker_analysis(
    document_id: str | None = None,
    document_ids: list[str] | None = None,
    query: str | None = None,
    top_k: int = 5,
    payload: AnalysisRequest | None = Body(default=None),
) -> dict:
    request = payload or AnalysisRequest(document_id=document_id, document_ids=document_ids, query=query, top_k=top_k)
    if request.document_id is None and not request.document_ids and not document_id and not document_ids:
        return _execute(analyze_blockers, query=request.query, top_k=request.top_k)
    return _execute(analyze_blockers,
        document_id=request.document_id or document_id,
        document_ids=request.document_ids or document_ids,
        query=request.query or query,
        top_k=request.top_k or top_k,
    )
