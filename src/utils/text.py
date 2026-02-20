from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(slots=True)
class ChunkedText:
    chunk_index: int
    text: str
    start_page: int
    end_page: int


def normalize_topic_key(title: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9а-яА-Я_\-\s]", " ", title)
    slug = re.sub(r"\s+", " ", slug).strip().lower().replace(" ", "_")
    return slug[:80] or "topic"


def sanitize_fts_query(query: str) -> str:
    tokens = re.findall(r"[\wа-яА-Я]+", query.lower())
    if not tokens:
        return ""
    return " OR ".join(tokens[:12])


def estimate_tokens(text: str) -> int:
    # Cheap estimate for budget management.
    return max(1, len(text) // 4)


def chunk_markdown_by_size(
    pages: list[tuple[int, str]], min_chars: int = 600, max_chars: int = 900
) -> list[ChunkedText]:
    chunks: list[ChunkedText] = []
    buffer_parts: list[str] = []
    buffer_pages: list[int] = []

    def flush() -> None:
        if not buffer_parts:
            return
        text = "\n\n".join(buffer_parts).strip()
        if not text:
            return
        chunks.append(
            ChunkedText(
                chunk_index=len(chunks),
                text=text,
                start_page=min(buffer_pages),
                end_page=max(buffer_pages),
            )
        )

    for page_number, text in pages:
        normalized = text.strip()
        if not normalized:
            continue

        candidate = "\n\n".join(buffer_parts + [normalized])
        if len(candidate) <= max_chars:
            buffer_parts.append(normalized)
            buffer_pages.append(page_number)
            continue

        if len("\n\n".join(buffer_parts)) >= min_chars:
            flush()
            buffer_parts = [normalized]
            buffer_pages = [page_number]
            continue

        # Keep a large single page as one chunk if it exceeds max_chars.
        if not buffer_parts:
            chunks.append(
                ChunkedText(
                    chunk_index=len(chunks),
                    text=normalized,
                    start_page=page_number,
                    end_page=page_number,
                )
            )
            continue

        flush()
        buffer_parts = [normalized]
        buffer_pages = [page_number]

    flush()
    return chunks
