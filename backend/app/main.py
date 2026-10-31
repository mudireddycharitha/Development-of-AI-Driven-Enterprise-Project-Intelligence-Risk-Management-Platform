import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import analysis, chunks, documents, health, search
from app.core.config import settings
from app.database.db import initialize_database

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
