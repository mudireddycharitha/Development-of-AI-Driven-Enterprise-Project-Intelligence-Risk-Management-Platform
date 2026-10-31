from typing import Any

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=50)
    document_id: str | None = None
    file_type: str | None = None


class SearchResult(BaseModel):
    rank: int
    chunk_id: str
    document_id: str
    filename: str
    file_type: str
    chunk_index: int
    text: str
    location: dict[str, Any]
    distance: float
    embedding_model: str


class PaginatedResponse(BaseModel):
    items: list[dict[str, Any]]
    total: int
    page: int
    page_size: int
    pages: int
