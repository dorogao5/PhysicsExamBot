from __future__ import annotations

import json
import logging
from typing import Any

from src.config.prompts import QUESTION_BANK_PROMPT, TOPIC_EXTRACTION_PROMPT
from src.llm.gateway import DashScopeChatClient
from src.models.schemas import QuestionCard, TopicNode
from src.storage.db import Database
from src.utils.text import normalize_topic_key

logger = logging.getLogger(__name__)


TOPIC_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "topics": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "summary": {"type": "string"},
                    "definitions": {"type": "array", "items": {"type": "string"}},
                    "formulas": {"type": "array", "items": {"type": "string"}},
                    "related": {"type": "array", "items": {"type": "string"}},
                    "prerequisites": {"type": "array", "items": {"type": "string"}},
                    "difficulty": {"type": "integer", "minimum": 1, "maximum": 3},
                },
                "required": [
                    "title",
                    "summary",
                    "definitions",
                    "formulas",
                    "related",
                    "prerequisites",
                    "difficulty",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["topics"],
    "additionalProperties": False,
}


QUESTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "scope_type": {"type": "string", "enum": ["core", "deep", "beyond"]},
                    "difficulty": {"type": "integer", "minimum": 1, "maximum": 3},
                    "question": {"type": "string"},
                    "ideal_answer": {"type": "string"},
                    "rubric": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["scope_type", "difficulty", "question", "ideal_answer", "rubric"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["questions"],
    "additionalProperties": False,
}


class KnowledgeBuilder:
    def __init__(self, db: Database, llm: DashScopeChatClient):
        self._db = db
        self._llm = llm

    async def build_topics(self, *, course_id: int) -> list[TopicNode]:
        chunks = await self._db.list_theory_chunks(course_id)
        merged: dict[str, TopicNode] = {}

        for chunk in chunks:
            source_text = (chunk["text"] or "").strip()
            if len(source_text) > 3200:
                source_text = source_text[:3200] + "\n...[truncated_for_extraction]"

            prompt = (
                "Фрагмент теормина:\n"
                f"{source_text}\n\n"
                "Верни только содержательные темы по этому фрагменту.\n"
                "Ограничения на ответ:\n"
                "- максимум 5 тем,\n"
                "- summary до 220 символов,\n"
                "- definitions до 3 пунктов,\n"
                "- formulas до 4 пунктов,\n"
                "- related и prerequisites до 4 пунктов.\n"
                "Без повторов."
            )

            try:
                raw = await self._llm.chat_json(
                    system_prompt=TOPIC_EXTRACTION_PROMPT,
                    user_prompt=prompt,
                    schema_name="topic_extraction",
                    schema=TOPIC_SCHEMA,
                    max_tokens=1200,
                    enable_thinking=False,
                )
            except Exception:
                logger.exception("Topic extraction failed for chunk=%s", chunk["chunk_index"])
                continue

            topics = raw.get("topics") or []
            for item in topics:
                title = (item.get("title") or "").strip()
                if not title:
                    continue
                topic_key = normalize_topic_key(title)
                topic = TopicNode(
                    course_id=course_id,
                    topic_key=topic_key,
                    title=title,
                    summary=(item.get("summary") or "").strip()[:1200],
                    definitions=[d.strip() for d in item.get("definitions", []) if d.strip()][:10],
                    formulas=[f.strip() for f in item.get("formulas", []) if f.strip()][:12],
                    related=[normalize_topic_key(r) for r in item.get("related", []) if r.strip()][:10],
                    prerequisites=[normalize_topic_key(p) for p in item.get("prerequisites", []) if p.strip()][:10],
                    difficulty=int(item.get("difficulty", 1)),
                )

                if topic_key not in merged:
                    merged[topic_key] = topic
                    continue

                # Merge duplicate topic mentions across chunks.
                existing = merged[topic_key]
                existing.summary = self._merge_summary(existing.summary, topic.summary)
                existing.definitions = _merge_lists(existing.definitions, topic.definitions, limit=15)
                existing.formulas = _merge_lists(existing.formulas, topic.formulas, limit=20)
                existing.related = _merge_lists(existing.related, topic.related, limit=15)
                existing.prerequisites = _merge_lists(existing.prerequisites, topic.prerequisites, limit=15)
                existing.difficulty = max(existing.difficulty, topic.difficulty)

        result = list(merged.values())
        if not result:
            fallback = TopicNode(
                course_id=course_id,
                topic_key="general_physics",
                title="Общая физика (fallback)",
                summary="Тема создана автоматически, потому что извлечение тем не дало результата.",
                definitions=[],
                formulas=[],
                related=[],
                prerequisites=[],
                difficulty=1,
            )
            result = [fallback]

        await self._db.replace_topic_nodes(course_id=course_id, topics=result)
        return result

    async def build_question_bank(self, *, course_id: int) -> list[QuestionCard]:
        nodes = await self._db.list_topic_nodes(course_id)
        all_questions: list[QuestionCard] = []

        for node in nodes:
            topic_key = node["topic_key"]
            title = node["title"]
            summary = node["summary"]
            definitions = json.loads(node["definitions_json"])
            formulas = json.loads(node["formulas_json"])

            prompt = (
                f"Тема: {title} ({topic_key})\n"
                f"Summary: {summary}\n"
                f"Definitions: {definitions}\n"
                f"Formulas: {formulas}\n\n"
                "Сделай минимум 6 вопросов: 3 core, 2 deep, 1 beyond.\n"
                "Ограничения: каждый question и ideal_answer до 260 символов; rubric до 4 пунктов."
            )
            try:
                raw = await self._llm.chat_json(
                    system_prompt=QUESTION_BANK_PROMPT,
                    user_prompt=prompt,
                    schema_name="question_bank",
                    schema=QUESTION_SCHEMA,
                    max_tokens=1600,
                    enable_thinking=False,
                )
                items = raw.get("questions") or []
            except Exception:
                logger.exception("Question generation failed for topic=%s", topic_key)
                items = []

            questions_for_topic: list[QuestionCard] = []
            for item in items:
                question = (item.get("question") or "").strip()
                ideal_answer = (item.get("ideal_answer") or "").strip()
                if not question or not ideal_answer:
                    continue
                questions_for_topic.append(
                    QuestionCard(
                        course_id=course_id,
                        topic_key=topic_key,
                        scope_type=item.get("scope_type", "core"),
                        difficulty=int(item.get("difficulty", 1)),
                        question=question,
                        ideal_answer=ideal_answer,
                        rubric=[r.strip() for r in item.get("rubric", []) if r.strip()][:8],
                        source_topic_keys=[topic_key],
                    )
                )

            if len(questions_for_topic) < 3:
                questions_for_topic.extend(_fallback_questions(course_id=course_id, topic_key=topic_key, title=title))

            all_questions.extend(questions_for_topic)

        await self._db.replace_question_bank(course_id=course_id, questions=all_questions)
        return all_questions

    @staticmethod
    def _merge_summary(current: str, incoming: str) -> str:
        if not incoming:
            return current
        if incoming in current:
            return current
        if current in incoming:
            return incoming[:1200]
        merged = f"{current} {incoming}".strip()
        return merged[:1200]


def _merge_lists(left: list[str], right: list[str], limit: int) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in [*left, *right]:
        key = value.strip()
        if not key or key in seen:
            continue
        out.append(key)
        seen.add(key)
        if len(out) >= limit:
            break
    return out


def _fallback_questions(*, course_id: int, topic_key: str, title: str) -> list[QuestionCard]:
    return [
        QuestionCard(
            course_id=course_id,
            topic_key=topic_key,
            scope_type="core",
            difficulty=1,
            question=f"Дайте строгое определение темы: {title}.",
            ideal_answer="Нужно дать корректное определение, физический смысл и ключевые обозначения.",
            rubric=["Корректное определение", "Физический смысл", "Обозначения и единицы"],
            source_topic_keys=[topic_key],
        ),
        QuestionCard(
            course_id=course_id,
            topic_key=topic_key,
            scope_type="deep",
            difficulty=2,
            question=f"Как тема {title} связана с соседними разделами курса?",
            ideal_answer="Нужно объяснить причинно-следственные связи и ограничения применимости.",
            rubric=["Связи между разделами", "Границы применимости", "Корректная терминология"],
            source_topic_keys=[topic_key],
        ),
        QuestionCard(
            course_id=course_id,
            topic_key=topic_key,
            scope_type="beyond",
            difficulty=3,
            question=f"Приведите расширенный пример применения {title} вне базового набора задач.",
            ideal_answer="Нужен пример, уравнения и объяснение допущений.",
            rubric=["Осмысленный пример", "Уравнения", "Допущения"],
            source_topic_keys=[topic_key],
        ),
    ]
