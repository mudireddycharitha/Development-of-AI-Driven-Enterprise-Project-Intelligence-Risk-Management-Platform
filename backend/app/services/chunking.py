import re
from dataclasses import dataclass
from typing import Any


TOKEN_PATTERN = re.compile(r"\w+|[^\w\s]", re.UNICODE)


@dataclass
class TextSegment:
    text: str
    location: dict[str, Any]


def approximate_token_count(text: str) -> int:
    return len(TOKEN_PATTERN.findall(text))


def chunk_segments(
    segments: list[TextSegment],
    chunk_size: int = 500,
    overlap: int = 50,
) -> list[dict[str, Any]]:
    if chunk_size <= 0 or overlap < 0 or overlap >= chunk_size:
        raise ValueError("Chunk size must be positive and overlap must be smaller than chunk size.")

    chunks: list[dict[str, Any]] = []
    current_words: list[str] = []
    current_location: dict[str, Any] = {}
    current_tokens = 0

    def append_chunk(words: list[str], location: dict[str, Any]) -> None:
        text = " ".join(words).strip()
        if text:
            chunks.append({
                "text": text,
                "token_count": approximate_token_count(text),
                "location": location,
            })

    for segment in segments:
        text = segment.text.strip()
        if not text:
            continue
        words = text.split()
        for word in words:
            cost = max(1, approximate_token_count(word))
            if current_words and current_tokens + cost > chunk_size:
                append_chunk(current_words, current_location)
                overlap_words: list[str] = []
                overlap_tokens = 0
                for previous in reversed(current_words):
                    previous_cost = max(1, approximate_token_count(previous))
                    if overlap_tokens + previous_cost > overlap:
                        break
                    overlap_words.insert(0, previous)
                    overlap_tokens += previous_cost
                current_words = overlap_words
                current_tokens = overlap_tokens
                current_location = dict(segment.location)
            if not current_words:
                current_location = dict(segment.location)
            current_words.append(word)
            current_tokens += cost
            if "line_end" in segment.location:
                current_location["line_end"] = segment.location["line_end"]

    append_chunk(current_words, current_location)
    return chunks
