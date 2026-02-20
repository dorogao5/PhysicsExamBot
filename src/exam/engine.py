from __future__ import annotations

import json
import random
from typing import Any

from src.config.prompts import ANSWER_GRADING_PROMPT, PERSONA_SYSTEM_PROMPT, SESSION_SUMMARY_PROMPT
from src.config.settings import Settings
from src.knowledge.retrieval import KnowledgeRetrieval
from src.llm.gateway import DashScopeChatClient
from src.storage.db import Database


GRADING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "score": {"type": "number", "minimum": 0, "maximum": 100},
        "max_score": {"type": "number", "minimum": 1, "maximum": 100},
        "verdict": {"type": "string", "enum": ["correct", "partial", "incorrect"]},
        "missing_points": {"type": "array", "items": {"type": "string"}},
        "feedback_short": {"type": "string"},
    },
    "required": ["score", "max_score", "verdict", "missing_points", "feedback_short"],
    "additionalProperties": False,
}


class ExamEngine:
    def __init__(self, *, db: Database, llm: DashScopeChatClient, retrieval: KnowledgeRetrieval, settings: Settings):
        self._db = db
        self._llm = llm
        self._retrieval = retrieval
        self._settings = settings

    async def start_exam(self, *, chat_id: int, user_id: int, course_id: int) -> str:
        active = await self._db.get_active_exam_session(chat_id=chat_id, user_id=user_id)
        if active:
            return "Экзамен уже идет. Ответьте на текущий вопрос или завершите /exam_stop."

        topics = await self._db.list_topic_nodes(course_id)
        if not topics:
            return "Не найден граф тем. Загрузите теормин заново через /upload_theory."

        start_topic = await self._select_start_topic(user_id=user_id, course_id=course_id, topics=topics)
        related = await self._retrieval.get_related_topics(course_id=course_id, topic_key=start_topic, depth=1)
        question_rows = await self._retrieval.sample_questions(
            course_id=course_id,
            topic_keys=related,
            limit=12,
            mix={"core": 0.55, "deep": 0.30, "beyond": 0.15},
            exclude_ids=None,
        )

        if not question_rows:
            return "Нет вопросов в банке. Повторно загрузите теормин и дождитесь построения вопросов."

        question_plan = [row["id"] for row in question_rows]
        current_question = question_rows[0]

        await self._db.create_exam_session(
            chat_id=chat_id,
            user_id=user_id,
            course_id=course_id,
            current_topic_key=start_topic,
            question_plan=question_plan[1:],
            current_question=current_question,
            covered_topics=[start_topic],
        )

        return await self._render_examiner_question(current_question, preface="Начинаем сдачу. Слушаю внимательно.")

    async def handle_answer(self, *, chat_id: int, user_id: int, student_answer: str) -> str | None:
        session = await self._db.get_active_exam_session(chat_id=chat_id, user_id=user_id)
        if not session:
            return None

        current_question = self._parse_current_question(session)
        if not current_question:
            return "Текущий вопрос потерян. Завершите /exam_stop и начните заново через /exam_start."

        course_id = int(session["course_id"])
        session_id = int(session["id"])
        question_plan = self._parse_json_list(session.get("question_plan_json"))
        covered_topics = self._parse_json_list(session.get("covered_topics_json"))

        assessment = await self.grade_answer(
            course_id=course_id,
            question=current_question,
            student_answer=student_answer,
        )

        await self._db.add_exam_turn(
            session_id=session_id,
            question_id=current_question.get("id"),
            question_text=current_question.get("question", ""),
            expected_answer=current_question.get("ideal_answer", ""),
            student_answer=student_answer,
            score=float(assessment["score"]),
            max_score=float(assessment.get("max_score", 100)),
            feedback=assessment["feedback_short"],
            topic_key=current_question.get("topic_key", ""),
            scope_type=current_question.get("scope_type", "core"),
        )

        await self._db.upsert_user_topic_stat(
            user_id=user_id,
            course_id=course_id,
            topic_key=current_question.get("topic_key", "general_physics"),
            score=float(assessment["score"]),
        )

        score_total = float(session["score_total"]) + float(assessment["score"])
        score_count = int(session["score_count"]) + 1
        asked_count = int(session["asked_count"]) + 1

        topic_key = current_question.get("topic_key") or session.get("current_topic_key")
        if topic_key and topic_key not in covered_topics:
            covered_topics.append(topic_key)

        if len(question_plan) < 3:
            extra = await self._refill_question_plan(
                course_id=course_id,
                session=session,
                base_topic=topic_key,
                covered_topics=covered_topics,
                current_plan=question_plan,
            )
            question_plan.extend(extra)

        next_question = None
        while question_plan and next_question is None:
            candidate_id = question_plan.pop(0)
            next_question = await self._db.get_question_by_id(candidate_id)

        if next_question is None:
            summary = await self._update_summary(session_id=session_id, fallback=True)
            await self._db.update_exam_session(
                session_id=session_id,
                score_total=score_total,
                score_count=score_count,
                asked_count=asked_count,
                session_summary=summary,
                current_topic_key=topic_key,
                question_plan=[],
                current_question=None,
                covered_topics=covered_topics,
            )
            return (
                f"Оценка: {assessment['score']:.1f}/100. {assessment['feedback_short']}\n"
                "Вопросы закончились. Завершите экзамен: /exam_stop"
            )

        summary = await self._update_summary(session_id=session_id, fallback=False)
        await self._db.update_exam_session(
            session_id=session_id,
            score_total=score_total,
            score_count=score_count,
            asked_count=asked_count,
            session_summary=summary,
            current_topic_key=next_question.get("topic_key"),
            question_plan=question_plan,
            current_question=next_question,
            covered_topics=covered_topics,
        )

        ask_text = await self._render_examiner_question(next_question, preface=assessment["feedback_short"])
        return f"Оценка: {assessment['score']:.1f}/100\n{ask_text}"

    async def stop_exam(self, *, chat_id: int, user_id: int) -> str:
        session = await self._db.get_active_exam_session(chat_id=chat_id, user_id=user_id)
        if not session:
            return "Активной сдачи нет. Запустите /exam_start."

        finished = await self._db.finish_exam_session(session_id=int(session["id"]))

        asked = int(finished["asked_count"])
        avg_score = float(finished["score_total"]) / max(1, int(finished["score_count"]))
        enough_questions = asked >= self._settings.min_questions_for_pass
        passed = enough_questions and avg_score >= self._settings.pass_threshold

        result = "СДАЛ" if passed else "НЕ СДАЛ"
        extra = ""
        if not enough_questions:
            extra = (
                f"\nНедостаточно вопросов для валидного зачета: {asked}/"
                f"{self._settings.min_questions_for_pass}."
            )

        return (
            f"Экзамен завершен.\n"
            f"Средний балл: {avg_score:.1f}/100\n"
            f"Вопросов оценено: {asked}\n"
            f"Итог: {result}.{extra}"
        )

    async def grade_answer(
        self,
        *,
        course_id: int,
        question: dict[str, Any],
        student_answer: str,
    ) -> dict[str, Any]:
        rubric = question.get("rubric")
        if rubric is None and isinstance(question.get("rubric_json"), str):
            try:
                rubric = json.loads(question["rubric_json"])
            except json.JSONDecodeError:
                rubric = []

        retrieval_chunks = await self._retrieval.lookup_theory(
            course_id=course_id,
            query=question.get("question", ""),
            top_k=self._settings.top_k_retrieval,
        )
        context = "\n\n".join(chunk.text for chunk in retrieval_chunks)
        context = context[: self._settings.max_context_chars]

        prompt = (
            f"Вопрос экзамена: {question.get('question', '')}\n"
            f"Идеальный ответ: {question.get('ideal_answer', '')}\n"
            f"Рубрика: {rubric or []}\n"
            f"Ответ студента: {student_answer}\n\n"
            f"Релевантный контекст курса:\n{context}"
        )

        try:
            graded = await self._llm.chat_json(
                system_prompt=ANSWER_GRADING_PROMPT,
                user_prompt=prompt,
                schema_name="turn_assessment",
                schema=GRADING_SCHEMA,
                max_tokens=900,
                enable_thinking=False,
            )
        except Exception:
            # Conservative fallback keeps exam flow alive even if grading call fails.
            graded = {
                "score": 0,
                "max_score": 100,
                "verdict": "incorrect",
                "missing_points": ["Ошибка оценивания: временно недоступен LLM"],
                "feedback_short": "Не удалось надежно оценить ответ, поставлен технический 0.",
            }

        return graded

    async def _render_examiner_question(self, question_row: dict[str, Any], preface: str) -> str:
        prompt = (
            f"Контекст: {preface}\n"
            f"Тема: {question_row.get('topic_key', '')}\n"
            f"Тип вопроса: {question_row.get('scope_type', 'core')}\n"
            f"База вопроса: {question_row.get('question', '')}\n"
            "Сформулируй реплику преподавателя в его манере.\n"
            "Требование: без дискриминации, угроз и призывов к насилию."
        )
        try:
            text = await self._llm.chat_text(
                system_prompt=PERSONA_SYSTEM_PROMPT,
                user_prompt=prompt,
                max_tokens=380,
                temperature=0.65,
                enable_thinking=False,
            )
            return text.strip()
        except Exception:
            return f"{preface}\nВопрос: {question_row.get('question', '')}"

    async def _select_start_topic(
        self,
        *,
        user_id: int,
        course_id: int,
        topics: list[dict[str, Any]],
    ) -> str:
        stats = await self._db.get_user_topic_stats_map(user_id=user_id, course_id=course_id)
        keys = [topic["topic_key"] for topic in topics]

        weights: list[float] = []
        for key in keys:
            row = stats.get(key)
            if row is None:
                weights.append(1.8)
                continue
            avg = float(row["avg_score"])
            weakness = max(0.0, (70 - avg) / 70)
            weights.append(1 + weakness)

        return random.choices(keys, weights=weights, k=1)[0]

    async def _refill_question_plan(
        self,
        *,
        course_id: int,
        session: dict[str, Any],
        base_topic: str | None,
        covered_topics: list[str],
        current_plan: list[int],
    ) -> list[int]:
        asked_ids = await self._db.list_session_question_ids(session_id=int(session["id"]))
        asked_ids.update(current_plan)

        topic_seed = base_topic or session.get("current_topic_key")
        if not topic_seed:
            nodes = await self._db.list_topic_nodes(course_id)
            if not nodes:
                return []
            topic_seed = random.choice(nodes)["topic_key"]

        topic_keys = await self._retrieval.get_related_topics(course_id=course_id, topic_key=topic_seed, depth=1)
        if len(topic_keys) < 3:
            topic_keys = list(dict.fromkeys(topic_keys + covered_topics))

        sampled = await self._retrieval.sample_questions(
            course_id=course_id,
            topic_keys=topic_keys,
            limit=8,
            mix={"core": 0.55, "deep": 0.30, "beyond": 0.15},
            exclude_ids=asked_ids,
        )
        return [row["id"] for row in sampled]

    async def _update_summary(self, *, session_id: int, fallback: bool) -> str:
        turns = await self._db.list_recent_turns(session_id=session_id, limit=6)
        if not turns:
            return ""

        short_log = []
        for turn in turns:
            short_log.append(
                f"Q: {turn['question_text']}\nA: {turn['student_answer']}\n"
                f"score={turn['score']:.1f} topic={turn['topic_key']}"
            )
        source = "\n\n".join(short_log)

        if fallback:
            return source[-500:]

        try:
            summary = await self._llm.chat_text(
                system_prompt=SESSION_SUMMARY_PROMPT,
                user_prompt=source,
                max_tokens=220,
                temperature=0.1,
                enable_thinking=False,
            )
            return summary[:500]
        except Exception:
            return source[-500:]

    @staticmethod
    def _parse_json_list(raw: str | None) -> list[Any]:
        if not raw:
            return []
        try:
            value = json.loads(raw)
            return value if isinstance(value, list) else []
        except json.JSONDecodeError:
            return []

    @staticmethod
    def _parse_current_question(session: dict[str, Any]) -> dict[str, Any] | None:
        raw = session.get("current_question_json")
        if not raw:
            return None
        try:
            value = json.loads(raw)
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            return None
