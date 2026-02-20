from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Awaitable, Callable

from src.ocr.pdf_renderer import render_pdf_to_pngs
from src.ocr.qwen_ocr import QwenOcrClient
from src.storage.db import Database
from src.utils.files import safe_write_text
from src.utils.text import chunk_markdown_by_size, estimate_tokens

ProgressCallback = Callable[[str], Awaitable[None]]


class OcrIngestionService:
    def __init__(self, db: Database, ocr_client: QwenOcrClient):
        self._db = db
        self._ocr = ocr_client

    async def ingest_course(
        self,
        *,
        course_id: int,
        pdf_path: Path,
        course_dir: Path,
        on_progress: ProgressCallback | None = None,
    ) -> Path:
        async def progress(message: str) -> None:
            if on_progress:
                await on_progress(message)

        rendered_dir = course_dir / "ocr" / "rendered"
        pages_json_dir = course_dir / "ocr" / "pages"
        rendered_dir.mkdir(parents=True, exist_ok=True)
        pages_json_dir.mkdir(parents=True, exist_ok=True)

        await progress("Рендерю страницы PDF в PNG...")
        page_images = await asyncio.to_thread(render_pdf_to_pngs, pdf_path, rendered_dir)
        await progress(f"Страниц для OCR: {len(page_images)}")

        existing_pages = await self._db.list_ocr_pages(course_id)
        existing_map = {int(row["page_number"]): row for row in existing_pages}
        page_text_pairs: list[tuple[int, str]] = []

        for idx, image_path in enumerate(page_images, start=1):
            existing = existing_map.get(idx)
            if existing and (existing.get("markdown_text") or "").strip():
                await progress(f"Использую кэш OCR страницы {idx}/{len(page_images)}")
                markdown_text = str(existing.get("markdown_text") or "")
                formula_text = str(existing.get("formula_text") or "")
            else:
                await progress(f"OCR страница {idx}/{len(page_images)}")
                document_result = await self._ocr.document_parsing(image_path)
                formula_result = await self._ocr.formula_recognition(image_path)

                markdown_text = self._ocr.extract_text(document_result)
                formula_text = self._ocr.extract_text(formula_result)

                raw_json_path = pages_json_dir / f"page_{idx:04d}.json"
                raw_json_path.write_text(
                    json.dumps(
                        {
                            "page_number": idx,
                            "document_parsing": document_result,
                            "formula_recognition": formula_result,
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )

                await self._db.upsert_ocr_page(
                    course_id=course_id,
                    page_number=idx,
                    markdown_text=markdown_text,
                    formula_text=formula_text,
                    raw_json_path=str(raw_json_path),
                )

            merged_text = markdown_text.strip()
            if formula_text.strip() and formula_text.strip() not in merged_text:
                merged_text = f"{merged_text}\n\nФормулы (доп. OCR):\n{formula_text.strip()}".strip()

            page_text_pairs.append((idx, merged_text))

        compiled_path = course_dir / "theory_compiled.md"
        compiled_md = self._build_compiled_markdown(page_text_pairs)
        safe_write_text(compiled_path, compiled_md)

        await progress("Чанкую теормин и строю FTS-индекс...")
        chunks = chunk_markdown_by_size(page_text_pairs)
        chunk_rows = [
            {
                "chunk_index": chunk.chunk_index,
                "text": chunk.text,
                "start_page": chunk.start_page,
                "end_page": chunk.end_page,
                "token_estimate": estimate_tokens(chunk.text),
            }
            for chunk in chunks
        ]
        await self._db.replace_theory_chunks(course_id=course_id, chunks=chunk_rows)

        await progress("OCR и индексирование завершены.")
        return compiled_path

    @staticmethod
    def _build_compiled_markdown(page_text_pairs: list[tuple[int, str]]) -> str:
        parts = ["# Теоретический минимум (OCR)"]
        for page_number, text in page_text_pairs:
            parts.append(f"\n## Страница {page_number}\n")
            parts.append(text.strip() or "(пусто)")
        return "\n\n".join(parts).strip() + "\n"
