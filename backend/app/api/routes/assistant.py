import logging
from time import perf_counter
from uuid import UUID

from fastapi import APIRouter, HTTPException

from app.schemas.milestone3 import AssistantConversation, AssistantRequest, AssistantResponse
from app.services.assistant import (
    ask_project_question,
    clear_conversation,
    get_conversation,
)
from app.services.project_agents import (
    LLMConfigurationError,
    LLMGenerationError,
    LLMTimeoutError,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/assistant", tags=["project intelligence assistant"])


@router.post("/chat", response_model=AssistantResponse)
def chat(payload: AssistantRequest) -> dict:
    started = perf_counter()
    try:
        response = ask_project_question(
            session_id=payload.session_id,
            question=payload.question,
            document_ids=payload.document_ids,
            top_k=payload.top_k,
        )
        logger.info(
            "Assistant API request completed duration_ms=%.1f answer_mode=%s sources=%d",
            (perf_counter() - started) * 1000,
            response["answer_mode"],
            len(response["sources"]),
        )
        return response
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except LLMConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except LLMTimeoutError as error:
        logger.warning(
            "Assistant API request timed out duration_ms=%.1f",
            (perf_counter() - started) * 1000,
        )
        raise HTTPException(
            status_code=504,
            detail={"code": "llm_timeout", "message": str(error)},
        ) from error
    except LLMGenerationError as error:
        logger.warning(
            "Assistant API generation failed duration_ms=%.1f",
            (perf_counter() - started) * 1000,
        )
        raise HTTPException(status_code=503, detail=str(error)) from error
    except Exception as error:
        logger.exception(
            "Project intelligence assistant request failed duration_ms=%.1f",
            (perf_counter() - started) * 1000,
        )
        raise HTTPException(
            status_code=503,
            detail="The assistant could not retrieve project evidence. Check embedding and vector-store availability.",
        ) from error


@router.get("/conversations/{session_id}", response_model=AssistantConversation)
def conversation(session_id: UUID) -> dict:
    return get_conversation(session_id)


@router.delete("/conversations/{session_id}")
def delete_conversation(session_id: UUID) -> dict[str, str]:
    clear_conversation(session_id)
    return {"status": "cleared"}
