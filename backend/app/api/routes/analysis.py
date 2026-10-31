from fastapi import APIRouter, Body, HTTPException

from app.schemas.analysis import AnalysisRequest
from app.services.project_agents import analyze_blockers, analyze_risks, analyze_scope

router = APIRouter(prefix="/analysis", tags=["analysis"])


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
        return analyze_scope(query=request.query, top_k=request.top_k)
    return analyze_scope(
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
        return analyze_risks(query=request.query, top_k=request.top_k)
    return analyze_risks(
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
        return analyze_blockers(query=request.query, top_k=request.top_k)
    return analyze_blockers(
        document_id=request.document_id or document_id,
        document_ids=request.document_ids or document_ids,
        query=request.query or query,
        top_k=request.top_k or top_k,
    )
