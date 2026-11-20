import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import analysis, assistant, chunks, documentation, documents, health, project_health, project_insights, search
from app.core.config import settings
from app.database.db import connect, initialize_database
from app.services.embedding_service import get_model

logger = logging.getLogger(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    for directory in (
        settings.uploads_dir, settings.extracted_dir, settings.chroma_dir,
        settings.database_path.parent,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    initialize_database()
    with connect() as connection:
        has_indexed_documents = connection.execute(
            "SELECT 1 FROM documents WHERE processing_status = 'indexed' LIMIT 1"
        ).fetchone()
    if has_indexed_documents:
        logger.info("Warming the embedding model for existing indexed documents.")
        await asyncio.to_thread(get_model)
    yield


app = FastAPI(
    title="Enterprise Project Intelligence Platform",
    description="Local document ingestion, vector indexing, and semantic retrieval.",
    version="1.0.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)
app.include_router(health.router)
app.include_router(documents.router)
app.include_router(chunks.router)
app.include_router(search.router)
app.include_router(analysis.router)
app.include_router(documentation.router)
app.include_router(project_health.router)
app.include_router(project_insights.router)
app.include_router(assistant.router)


@app.get("/", tags=["system"])
def root() -> dict[str, str]:
    system_health = health.health()
    return {
        "application": app.title,
        "version": app.version,
        "status": system_health["status"],
        "api_status": "online",
        "health_url": "/health",
        "docs_url": "/docs",
    }
