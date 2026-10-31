import json
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator

from app.core.config import settings


def initialize_database() -> None:
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    with connect() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                stored_filename TEXT NOT NULL,
                file_type TEXT NOT NULL,
                file_size INTEGER NOT NULL,
                sha256 TEXT NOT NULL,
                uploaded_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                processing_status TEXT NOT NULL,
                extraction_error TEXT,
                failed_stage TEXT,
                extracted_text TEXT NOT NULL DEFAULT '',
                chunk_count INTEGER NOT NULL DEFAULT 0,
                indexed_chunk_count INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_documents_uploaded_at ON documents(uploaded_at);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_documents_sha256 ON documents(sha256);
            CREATE TABLE IF NOT EXISTS chunks (
                id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                chunk_index INTEGER NOT NULL,
                text TEXT NOT NULL,
                token_count INTEGER NOT NULL,
                location_json TEXT NOT NULL DEFAULT '{}',
                embedding_status TEXT NOT NULL DEFAULT 'pending',
                indexing_status TEXT NOT NULL DEFAULT 'pending',
                embedding_dimensions INTEGER,
                embedding_model TEXT,
                UNIQUE(document_id, chunk_index)
            );
            CREATE INDEX IF NOT EXISTS idx_chunks_document_id ON chunks(document_id, chunk_index);
            CREATE TABLE IF NOT EXISTS analysis_results (
                id TEXT PRIMARY KEY,
                analysis_type TEXT NOT NULL,
                document_ids TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                result_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_analysis_results_type ON analysis_results(analysis_type, created_at);
            """
        )
        connection.execute("PRAGMA foreign_keys = ON")


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(settings.database_path), timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def chunk_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(row)
    result["location"] = json.loads(result.pop("location_json"))
    return result
