import threading
from typing import Any, Sequence

import numpy as np

from app.core.config import settings

_model: Any = None
_model_name: str | None = None
_lock = threading.Lock()


def get_model() -> Any:
    global _model, _model_name
    if _model is None or _model_name != settings.embedding_model:
        with _lock:
            if _model is None or _model_name != settings.embedding_model:
                from sentence_transformers import SentenceTransformer
                _model = SentenceTransformer(settings.embedding_model)
                _model_name = settings.embedding_model
    return _model


def embed_texts(texts: Sequence[str]) -> list[list[float]]:
    if not texts or any(not text.strip() for text in texts):
        raise ValueError("Embedding input must contain nonempty text.")
    vectors = get_model().encode(
        list(texts), convert_to_numpy=True, normalize_embeddings=True,
        show_progress_bar=False,
    )
    result = np.asarray(vectors, dtype=np.float32)
    if result.ndim != 2 or result.shape[0] != len(texts) or result.shape[1] == 0:
        raise RuntimeError("The embedding model returned an invalid vector shape.")
    if not np.isfinite(result).all():
        raise RuntimeError("The embedding model returned non-finite values.")
    return result.tolist()


def model_name() -> str:
    return settings.embedding_model
