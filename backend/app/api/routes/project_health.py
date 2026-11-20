import logging

from fastapi import APIRouter, HTTPException

from app.schemas.milestone3 import ProjectHealthResult
from app.services.project_health import calculate_project_health, get_latest_project_health

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/project-health", tags=["project health"])


@router.post("/recalculate", response_model=ProjectHealthResult)
def recalculate() -> dict:
    try:
        return calculate_project_health()
    except Exception as error:
        logger.exception("Project health calculation failed")
        raise HTTPException(status_code=503, detail="Project health could not be calculated.") from error


@router.get("/latest", response_model=ProjectHealthResult)
def latest() -> dict:
    result = get_latest_project_health()
    if result is None:
        raise HTTPException(status_code=404, detail="No saved project health calculation was found.")
    return result
