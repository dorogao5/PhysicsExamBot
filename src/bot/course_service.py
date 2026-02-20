from __future__ import annotations

import asyncio
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Awaitable, Callable

from src.knowledge.builder import KnowledgeBuilder
from src.ocr.ingestion import OcrIngestionService
from src.storage.db import Database
from src.utils.files import sha256_file

ProgressCallback = Callable[[str], Awaitable[None]]


class CourseBuildResult(dict):
    @property
    def course_id(self) -> int:
        return int(self["course_id"])


class CourseService:
    def __init__(
        self,
        *,
        db: Database,
        ingestion: OcrIngestionService,
        knowledge_builder: KnowledgeBuilder,
        courses_root: Path,
    ):
        self._db = db
        self._ingestion = ingestion
        self._knowledge_builder = knowledge_builder
        self._courses_root = courses_root
        self._hash_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._course_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def process_uploaded_pdf(
        self,
        *,
        chat_id: int,
        user_id: int,
        title: str,
        uploaded_path: Path,
        on_progress: ProgressCallback | None = None,
    ) -> CourseBuildResult:
        async def progress(text: str) -> None:
            if on_progress:
                await on_progress(text)

        digest = sha256_file(uploaded_path)
        async with self._hash_locks[digest]:
            course_row, created = await self._db.create_or_get_course(
                chat_id=chat_id,
                user_id=user_id,
                title=title,
                pdf_sha256=digest,
                pdf_path=str(uploaded_path),
            )

        course_id = int(course_row["id"])
        await self._db.subscribe_user_to_course(chat_id=chat_id, user_id=user_id, course_id=course_id)
        await self._db.set_selected_course(chat_id=chat_id, user_id=user_id, course_id=course_id)

        if not created and course_row.get("status") == "ready":
            return CourseBuildResult(
                {
                    "course_id": course_id,
                    "reused": True,
                    "processing": False,
                    "topics_count": len(await self._db.list_topic_nodes(course_id)),
                    "questions_count": await self._db.count_question_bank(course_id),
                }
            )

        lock = self._course_locks[course_id]
        if lock.locked():
            return CourseBuildResult(
                {
                    "course_id": course_id,
                    "reused": False,
                    "processing": True,
                    "topics_count": 0,
                    "questions_count": 0,
                }
            )

        async with lock:
            current = await self._db.get_course_by_id(course_id)
            if current and current.get("status") == "ready":
                return CourseBuildResult(
                    {
                        "course_id": course_id,
                        "reused": True,
                        "processing": False,
                        "topics_count": len(await self._db.list_topic_nodes(course_id)),
                        "questions_count": await self._db.count_question_bank(course_id),
                    }
                )

            course_dir = self._courses_root / str(course_id)
            course_dir.mkdir(parents=True, exist_ok=True)

            pdf_target = course_dir / "theory.pdf"
            if created or not pdf_target.exists():
                shutil.copy2(uploaded_path, pdf_target)
                await self._db.update_course_pdf_path(course_id=course_id, pdf_path=str(pdf_target))

            await self._db.update_course_status(course_id=course_id, status="processing")
            await progress("Начинаю OCR и сбор теормина...")

            try:
                compiled_path = await self._ingestion.ingest_course(
                    course_id=course_id,
                    pdf_path=pdf_target,
                    course_dir=course_dir,
                    on_progress=on_progress,
                )

                await self._db.update_course_status(
                    course_id=course_id,
                    status="indexing",
                    compiled_markdown_path=str(compiled_path),
                )
                await progress("Строю граф тем...")
                topics = await self._knowledge_builder.build_topics(course_id=course_id)

                await progress("Генерирую банк вопросов...")
                questions = await self._knowledge_builder.build_question_bank(course_id=course_id)

                await self._db.update_course_status(course_id=course_id, status="ready")
                await progress("Курс готов к сдаче экзамена. Запускайте /exam_start")
            except Exception:
                await self._db.update_course_status(course_id=course_id, status="failed")
                raise

            return CourseBuildResult(
                {
                    "course_id": course_id,
                    "reused": False,
                    "processing": False,
                    "topics_count": len(topics),
                    "questions_count": len(questions),
                }
            )
