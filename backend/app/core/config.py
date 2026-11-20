import json
import math
import os
from pathlib import Path
from urllib.error import URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

from dotenv import load_dotenv


_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_ENV_FILE = _PROJECT_ROOT / ".env"
load_dotenv(_ENV_FILE, override=False)


class Settings:
    @property
    def project_root(self) -> Path:
        return _PROJECT_ROOT

    @property
    def env_file(self) -> Path:
        return self.project_root / ".env"

    @staticmethod
    def _secret_is_configured(value: str) -> bool:
        normalized = value.strip().casefold()
        if not normalized or any(marker in normalized for marker in (
            "your-", "replace-", "replace_me", "changeme", "placeholder",
            "your_api_key", "<", ">",
        )):
            return False
        return True

    @staticmethod
    def _get_json(url: str, headers: dict[str, str] | None = None) -> dict:
        request = Request(url, headers=headers or {}, method="GET")
        with urlopen(request, timeout=2.0) as response:
            return json.loads(response.read().decode("utf-8"))

    def ai_provider_status(self, check_connectivity: bool = False) -> dict[str, str | bool | None]:
        provider = self.ai_provider
        missing: list[str] = []
        if provider == "openai":
            if not self.openai_api_key_configured:
                missing.append("OPENAI_API_KEY")
            model = self.openai_model
        elif provider == "azure":
            azure_url = urlparse(self.azure_openai_endpoint)
            if (
                azure_url.scheme not in {"http", "https"}
                or not azure_url.hostname
                or azure_url.username
                or azure_url.password
            ):
                missing.append("AZURE_OPENAI_ENDPOINT")
            if not self.azure_openai_api_key_configured:
                missing.append("AZURE_OPENAI_API_KEY")
            if not self.azure_openai_deployment:
                missing.append("AZURE_OPENAI_DEPLOYMENT")
            model = self.azure_openai_deployment or "Azure deployment"
        elif provider == "ollama":
            ollama_url = urlparse(self.ollama_base_url)
            if (
                ollama_url.scheme not in {"http", "https"}
                or not ollama_url.hostname
                or ollama_url.username
                or ollama_url.password
            ):
                missing.append("OLLAMA_BASE_URL (must use http or https)")
            if not self.ollama_model:
                missing.append("OLLAMA_MODEL")
            model = self.ollama_model or "No model selected"
        elif provider == "rule_based":
            return {
                "provider": provider,
                "model": "Rule-based agents only",
                "status": "unavailable",
                "configured": False,
                "operational": False,
                "connectivity": "not_applicable",
                "message": (
                    "Rule-based analysis is available, but it does not generate conversational answers. "
                    "Configure Ollama, OpenAI, or Azure OpenAI for assistant chat."
                ),
            }
        else:
            return {
                "provider": provider,
                "model": "Unknown",
                "status": "unavailable",
                "configured": False,
                "operational": False,
                "connectivity": "not_checked",
                "message": "Unsupported AI_PROVIDER. Choose ollama, openai, or azure.",
            }

        if missing:
            message = (
                f"Missing {', '.join(missing)}. Set these in the repository-root .env "
                "file or the backend process environment, then restart FastAPI."
            )
            if provider == "ollama":
                message += (
                    " Install and start Ollama and manually download a model first; "
                    "the platform never downloads models automatically."
                )
            return {
                "provider": provider,
                "model": model,
                "status": "unavailable",
                "configured": False,
                "operational": False,
                "connectivity": "not_checked",
                "message": message,
            }

        result: dict[str, str | bool | None] = {
            "provider": provider,
            "model": model,
            "status": "configured",
            "configured": True,
            "operational": None,
            "connectivity": "not_checked",
            "message": (
                "Configuration is present; provider availability has not been checked."
            ),
        }
        if not check_connectivity:
            return result

        try:
            if provider == "openai":
                response = self._get_json(
                    "https://api.openai.com/v1/models",
                    {"Authorization": f"Bearer {self.openai_api_key}"},
                )
                available = {
                    item.get("id")
                    for item in response.get("data", [])
                    if isinstance(item, dict)
                }
                if self.openai_model not in available:
                    raise ValueError("The configured model was not returned by the provider.")
            elif provider == "ollama":
                response = self._get_json(
                    f"{self.ollama_base_url.rstrip('/')}/api/tags"
                )
                available = {
                    item.get("name")
                    for item in response.get("models", [])
                    if isinstance(item, dict)
                }
                if self.ollama_model not in available:
                    result.update({
                        "status": "unavailable",
                        "operational": False,
                        "connectivity": "reachable",
                        "message": (
                            f"Ollama is reachable, but model '{self.ollama_model}' is not installed. "
                            "Install Ollama and download that model manually, then refresh status."
                        ),
                    })
                    return result
            else:
                endpoint = self.azure_openai_endpoint.rstrip("/")
                deployment = quote(self.azure_openai_deployment, safe="")
                url = f"{endpoint}/openai/deployments/{deployment}?api-version=2024-02-01"
                self._get_json(url, {"api-key": self.azure_openai_api_key})
            result.update({
                "status": "operational",
                "operational": True,
                "connectivity": "reachable",
                "message": "The configured provider and model are reachable.",
            })
        except (OSError, URLError, TimeoutError, ValueError, json.JSONDecodeError):
            message = (
                "Configuration is present, but the provider or selected model could not be verified. "
                "Check service availability, credentials, endpoint, model name, and backend logs."
            )
            if provider == "ollama":
                message = (
                    "Ollama is not reachable. Install and start Ollama, then manually download the "
                    "configured model; model downloads are never started automatically."
                )
            result.update({
                "status": "unavailable",
                "operational": False,
                "connectivity": "unavailable",
                "message": message,
            })
        return result

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
    def llm_request_timeout_seconds(self) -> float:
        raw_timeout = os.getenv("LLM_REQUEST_TIMEOUT_SECONDS", "90")
        try:
            timeout = float(raw_timeout)
        except ValueError as error:
            raise ValueError("LLM_REQUEST_TIMEOUT_SECONDS must be a positive number.") from error
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("LLM_REQUEST_TIMEOUT_SECONDS must be a positive number.")
        return timeout

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in os.getenv(
            "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
        ).split(",") if origin.strip()]

    @property
    def ai_provider(self) -> str:
        return os.getenv("AI_PROVIDER", "openai").strip().lower() or "openai"

    @property
    def openai_api_key(self) -> str:
        return os.getenv("OPENAI_API_KEY", "").strip()

    @property
    def openai_api_key_configured(self) -> bool:
        return self._secret_is_configured(self.openai_api_key)

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
    def azure_openai_api_key_configured(self) -> bool:
        return self._secret_is_configured(self.azure_openai_api_key)

    @property
    def azure_openai_deployment(self) -> str:
        return os.getenv("AZURE_OPENAI_DEPLOYMENT", "").strip()

    @property
    def ollama_base_url(self) -> str:
        return os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").strip().rstrip("/")

    @property
    def ollama_model(self) -> str:
        return os.getenv("OLLAMA_MODEL", "").strip()


settings = Settings()
