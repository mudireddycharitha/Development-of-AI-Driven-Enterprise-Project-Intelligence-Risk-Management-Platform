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
                document_category TEXT NOT NULL DEFAULT 'General Project Document',
                document_title TEXT,
                document_description TEXT,
                project_name TEXT,
                milestone_name TEXT,
                change_type TEXT NOT NULL DEFAULT 'new',
                last_processed_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                processing_status TEXT NOT NULL,
                extraction_error TEXT,
                failed_stage TEXT,
                extracted_text TEXT NOT NULL DEFAULT '',
                chunk_count INTEGER NOT NULL DEFAULT 0,
                indexed_chunk_count INTEGER NOT NULL DEFAULT 0,
                version_of_document_id TEXT,
                document_version INTEGER NOT NULL DEFAULT 1,
                is_current_version INTEGER NOT NULL DEFAULT 1
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
            CREATE TABLE IF NOT EXISTS documentation_generations (
                id TEXT PRIMARY KEY,
                generation_key TEXT NOT NULL UNIQUE,
                document_ids TEXT NOT NULL DEFAULT '[]',
                generated_at TEXT NOT NULL,
                result_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS project_health_scores (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                result_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_project_health_created_at
                ON project_health_scores(created_at);
            CREATE TABLE IF NOT EXISTS assistant_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                content TEXT NOT NULL,
                sources_json TEXT NOT NULL DEFAULT '[]',
                answer_mode TEXT,
                health_json TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_assistant_messages_session
                ON assistant_messages(session_id, id);
            """
        )
        document_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(documents)").fetchall()
        }
        if "version_of_document_id" not in document_columns:
            connection.execute("ALTER TABLE documents ADD COLUMN version_of_document_id TEXT")
        if "document_version" not in document_columns:
            connection.execute(
                "ALTER TABLE documents ADD COLUMN document_version INTEGER NOT NULL DEFAULT 1"
            )
        if "is_current_version" not in document_columns:
            connection.execute(
                "ALTER TABLE documents ADD COLUMN is_current_version INTEGER NOT NULL DEFAULT 1"
            )
        incremental_columns = {
            "document_category": "TEXT NOT NULL DEFAULT 'General Project Document'",
            "document_title": "TEXT",
            "document_description": "TEXT",
            "project_name": "TEXT",
            "milestone_name": "TEXT",
            "change_type": "TEXT NOT NULL DEFAULT 'new'",
            "last_processed_at": "TEXT",
        }
        for column, definition in incremental_columns.items():
            if column not in document_columns:
                connection.execute(
                    f"ALTER TABLE documents ADD COLUMN {column} {definition}"
                )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_documents_current_version ON documents(is_current_version, processing_status)"
        )
        assistant_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(assistant_messages)").fetchall()
        }
        if "answer_mode" not in assistant_columns:
            connection.execute("ALTER TABLE assistant_messages ADD COLUMN answer_mode TEXT")
        if "health_json" not in assistant_columns:
            connection.execute("ALTER TABLE assistant_messages ADD COLUMN health_json TEXT")
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
