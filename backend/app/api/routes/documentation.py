import csv
import io
import json
import logging
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from app.schemas.milestone3 import DocumentationResult, DocumentSelectionRequest
from app.services.documentation import (
    generate_documentation,
    get_documentation,
    get_latest_documentation,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/documentation", tags=["documentation generation"])


@router.post("/generate", response_model=DocumentationResult)
def generate(payload: DocumentSelectionRequest) -> dict:
    try:
        return generate_documentation(payload.document_ids)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except Exception as error:
        logger.exception("Documentation generation failed")
        raise HTTPException(status_code=503, detail="Documentation generation could not complete.") from error


@router.get("/latest", response_model=DocumentationResult)
def latest(document_ids: list[str] | None = Query(default=None)) -> dict:
    result = get_latest_documentation(document_ids)
    if result is None:
        raise HTTPException(status_code=404, detail="No saved documentation generation was found.")
    return result


@router.get("/{generation_id}", response_model=DocumentationResult)
def get_generation(generation_id: str) -> dict:
    result = get_documentation(generation_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Documentation generation not found.")
    return result


def _export_rows(result: dict) -> list[dict[str, str]]:
    rows = []
    for collection, items in (
        ("user_story", result["user_stories"]),
        ("risk", result["risk_register"]),
        ("action_item", result["action_items"]),
        ("pending_decision", result["pending_decisions"]),
    ):
        for item in items:
            evidence = item.get("evidence") or {}
            rows.append({
                "type": collection,
                "id": item.get("id", ""),
                "title": item.get("title", ""),
                "description": item.get("description", ""),
                "user_role": item.get("user_role", ""),
                "priority": item.get("priority", ""),
                "status": item.get("status", ""),
                "assignee": item.get("assignee") or item.get("responsible_person") or "",
                "due_date": item.get("due_date") or "",
                "source_document": item.get("source_document", ""),
                "category": item.get("category", ""),
                "risk_level": item.get("risk_level", ""),
                "likelihood": item.get("likelihood", ""),
                "impact": item.get("impact", ""),
                "mitigation": item.get("suggested_mitigation", ""),
                "acceptance_criteria": json.dumps(item.get("acceptance_criteria", []), ensure_ascii=False),
                "evidence_excerpt": evidence.get("excerpt", ""),
            })
    return rows


@router.get("/{generation_id}/export")
def export_generation(
    generation_id: str,
    export_format: Literal["csv", "json"] = Query(default="json", alias="format"),
) -> Response:
    result = get_documentation(generation_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Documentation generation not found.")
    if export_format == "json":
        content = json.dumps(result, ensure_ascii=False, indent=2)
        media_type = "application/json"
    else:
        output = io.StringIO(newline="")
        rows = _export_rows(result)
        fieldnames = [
            "type", "id", "title", "description", "user_role", "priority", "status",
            "assignee", "due_date", "source_document", "category", "risk_level",
            "likelihood", "impact", "mitigation", "acceptance_criteria", "evidence_excerpt",
        ]
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
        content = output.getvalue()
        media_type = "text/csv; charset=utf-8"
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="project-documentation-{generation_id}.{export_format}"'},
    )
