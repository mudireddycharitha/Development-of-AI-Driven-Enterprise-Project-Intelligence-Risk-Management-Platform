import os
from pathlib import Path

from dotenv import load_dotenv


load_dotenv(Path(__file__).resolve().parents[3] / ".env", override=False)


class Settings:
    @property
    def project_root(self) -> Path:
        return Path(__file__).resolve().parents[3]

    @property
    def data_dir(self) -> Path:
        return Path(os.getenv("APP_DATA_DIR", str(self.project_root / "data"))).resolve()

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def extracted_dir(self) -> Path:
        return self.data_dir / "extracted_text"

    @property
    def chroma_dir(self) -> Path:
        return self.data_dir / "chroma_db"

    @property
    def database_path(self) -> Path:
        return self.data_dir / "metadata" / "platform.sqlite3"

    @property
    def max_file_size(self) -> int:
        return int(os.getenv("MAX_FILE_SIZE_MB", "50")) * 1024 * 1024

    @property
    def max_batch_files(self) -> int:
        return int(os.getenv("MAX_BATCH_FILES", "25"))

    @property
    def max_extracted_chars(self) -> int:
        return int(os.getenv("MAX_EXTRACTED_CHARS", "10000000"))

    @property
    def max_chunks_per_document(self) -> int:
        return int(os.getenv("MAX_CHUNKS_PER_DOCUMENT", "20000"))

    @property
    def chunk_size(self) -> int:
        return int(os.getenv("CHUNK_SIZE_TOKENS", "500"))

    @property
    def chunk_overlap(self) -> int:
        return int(os.getenv("CHUNK_OVERLAP_TOKENS", "50"))

    @property
    def embedding_model(self) -> str:
        return os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in os.getenv(
            "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
        ).split(",") if origin.strip()]

    @property
    def ai_provider(self) -> str:
        return os.getenv("AI_PROVIDER", "rule_based").strip().lower() or "rule_based"

    @property
    def openai_api_key(self) -> str:
        return os.getenv("OPENAI_API_KEY", "").strip()

    @property
    def openai_model(self) -> str:
        return os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"

    @property
    def azure_openai_endpoint(self) -> str:
        return os.getenv("AZURE_OPENAI_ENDPOINT", "").strip()

    @property
    def azure_openai_api_key(self) -> str:
        return os.getenv("AZURE_OPENAI_API_KEY", "").strip()

    @property
    def azure_openai_deployment(self) -> str:
        return os.getenv("AZURE_OPENAI_DEPLOYMENT", "").strip()


settings = Settings()
