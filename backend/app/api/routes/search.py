import logging

from fastapi import APIRouter, HTTPException

from app.schemas.api import SearchRequest
from app.services.embedding_service import embed_texts, model_name
from app.services.vector_store import query

router = APIRouter(tags=["search"])
logger = logging.getLogger(__name__)


@router.post("/search")
def semantic_search(request: SearchRequest) -> dict:
    normalized_query = request.query.strip()
    if not normalized_query:
        raise HTTPException(status_code=422, detail="Enter a nonempty search query.")
    try:
        vector = embed_texts([normalized_query])[0]
    except ModuleNotFoundError as error:
        logger.exception("Semantic search could not load an embedding dependency")
        dependency = error.name or "a required inference dependency"
        raise HTTPException(
            status_code=503,
            detail=(
                f"Embedding model '{model_name()}' is unavailable because Python dependency "
                f"'{dependency}' is not installed. Install backend requirements and restart the API."
            ),
        ) from error
    except ImportError as error:
        logger.exception("Semantic search could not import the embedding runtime")
        raise HTTPException(
            status_code=503,
            detail=(
                f"The embedding runtime for model '{model_name()}' is unavailable. "
                "Install backend requirements and restart the API."
            ),
        ) from error
    except Exception as error:
        logger.exception("Semantic search could not embed the query")
        raise HTTPException(
            status_code=503,
            detail=(
                f"Embedding model '{model_name()}' could not convert the query into a vector. "
                "Check that the model is available and its inference dependencies are working."
            ),
        ) from error
    try:
        matches = query(vector, request.top_k, request.document_id, request.file_type)
    except Exception as error:
        logger.exception("Semantic search could not query the vector store")
        raise HTTPException(
            status_code=503,
            detail=(
                "Vector search is unavailable because ChromaDB could not query the indexed "
                f"document collection. Check vector-store health and compatibility with "
                f"embedding model '{model_name()}'."
            ),
        ) from error
    results = []
    for rank, match in enumerate(matches, start=1):
        metadata = match["metadata"]
        location = {
            key: metadata[key] for key in ("page_number", "sheet_name", "row_number",
                                            "slide_number", "line_start", "line_end")
            if key in metadata
        }
        results.append({
            "rank": rank,
            "chunk_id": match["id"],
            "document_id": metadata.get("document_id", ""),
            "filename": metadata.get("filename", ""),
            "file_type": metadata.get("file_type", ""),
            "chunk_index": metadata.get("chunk_index", 0),
            "text": match["text"],
            "location": location,
            "distance": match["distance"],
            "embedding_model": model_name(),
        })
    return {"query": normalized_query, "results": results, "count": len(results)}
