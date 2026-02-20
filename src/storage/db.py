from __future__ import annotations

import json
import random
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import aiosqlite

from src.models.schemas import QuestionCard, TopicNode
from src.utils.text import sanitize_fts_query


@dataclass(slots=True)
class SearchChunk:
    id: int
    text: str
    start_page: int
    end_page: int
    rank: float


class Database:
    def __init__(self, path: str):
        self._path = path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL;")
        await self._conn.execute("PRAGMA foreign_keys=ON;")
        await self._conn.execute("PRAGMA synchronous=NORMAL;")

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database is not connected")
        return self._conn

    async def init_schema(self) -> None:
        await self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS courses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                pdf_sha256 TEXT NOT NULL,
                pdf_path TEXT NOT NULL,
                compiled_markdown_path TEXT,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(chat_id, user_id, pdf_sha256)
            );

            CREATE TABLE IF NOT EXISTS course_subscribers (
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(chat_id, user_id, course_id)
            );

            CREATE TABLE IF NOT EXISTS user_selected_course (
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(chat_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS ocr_pages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
                page_number INTEGER NOT NULL,
                markdown_text TEXT NOT NULL,
                formula_text TEXT,
                raw_json_path TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(course_id, page_number)
            );

            CREATE TABLE IF NOT EXISTS theory_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
                chunk_index INTEGER NOT NULL,
                text TEXT NOT NULL,
                start_page INTEGER NOT NULL,
                end_page INTEGER NOT NULL,
                token_estimate INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(course_id, chunk_index)
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS theory_chunks_fts
            USING fts5(text, content='theory_chunks', content_rowid='id');

            CREATE TRIGGER IF NOT EXISTS theory_chunks_ai AFTER INSERT ON theory_chunks BEGIN
              INSERT INTO theory_chunks_fts(rowid, text) VALUES (new.id, new.text);
            END;

            CREATE TRIGGER IF NOT EXISTS theory_chunks_ad AFTER DELETE ON theory_chunks BEGIN
              INSERT INTO theory_chunks_fts(theory_chunks_fts, rowid, text) VALUES('delete', old.id, old.text);
            END;

            CREATE TRIGGER IF NOT EXISTS theory_chunks_au AFTER UPDATE ON theory_chunks BEGIN
              INSERT INTO theory_chunks_fts(theory_chunks_fts, rowid, text) VALUES('delete', old.id, old.text);
              INSERT INTO theory_chunks_fts(rowid, text) VALUES (new.id, new.text);
            END;

            CREATE TABLE IF NOT EXISTS topic_nodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
                topic_key TEXT NOT NULL,
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                definitions_json TEXT NOT NULL,
                formulas_json TEXT NOT NULL,
                related_json TEXT NOT NULL,
                prerequisites_json TEXT NOT NULL,
                difficulty INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(course_id, topic_key)
            );

            CREATE TABLE IF NOT EXISTS question_bank (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
                topic_key TEXT NOT NULL,
                scope_type TEXT NOT NULL,
                difficulty INTEGER NOT NULL DEFAULT 1,
                question TEXT NOT NULL,
                ideal_answer TEXT NOT NULL,
                rubric_json TEXT NOT NULL,
                source_topic_keys_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS exam_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
                status TEXT NOT NULL,
                score_total REAL NOT NULL DEFAULT 0,
                score_count INTEGER NOT NULL DEFAULT 0,
                asked_count INTEGER NOT NULL DEFAULT 0,
                session_summary TEXT NOT NULL DEFAULT '',
                current_topic_key TEXT,
                question_plan_json TEXT NOT NULL DEFAULT '[]',
                current_question_json TEXT,
                covered_topics_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                ended_at TEXT
            );

            CREATE TABLE IF NOT EXISTS exam_turns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL REFERENCES exam_sessions(id) ON DELETE CASCADE,
                question_id INTEGER,
                question_text TEXT NOT NULL,
                expected_answer TEXT NOT NULL,
                student_answer TEXT NOT NULL,
                score REAL NOT NULL,
                max_score REAL NOT NULL,
                feedback TEXT NOT NULL,
                topic_key TEXT NOT NULL,
                scope_type TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS user_topic_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
                topic_key TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                avg_score REAL NOT NULL DEFAULT 0,
                last_score REAL NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, course_id, topic_key)
            );
            """
        )
        await self.conn.commit()

    async def create_or_get_course(
        self,
        *,
        chat_id: int,
        user_id: int,
        title: str,
        pdf_sha256: str,
        pdf_path: str,
    ) -> tuple[dict[str, Any], bool]:
        existing = await self.get_course_by_hash_global(pdf_sha256=pdf_sha256)
        if existing:
            return existing, False

        cursor = await self.conn.execute(
            """
            INSERT INTO courses(chat_id, user_id, title, pdf_sha256, pdf_path, status)
            VALUES (?, ?, ?, ?, ?, 'processing')
            """,
            (chat_id, user_id, title, pdf_sha256, pdf_path),
        )
        await self.conn.commit()
        course_id = cursor.lastrowid
        created = await self.get_course_by_id(course_id)
        if not created:
            raise RuntimeError("Failed to create course")
        return created, True

    async def get_course_by_hash_global(self, *, pdf_sha256: str) -> dict[str, Any] | None:
        cursor = await self.conn.execute(
            """
            SELECT * FROM courses
            WHERE pdf_sha256=?
            ORDER BY
              CASE status
                WHEN 'ready' THEN 0
                WHEN 'indexing' THEN 1
                WHEN 'processing' THEN 2
                ELSE 3
              END ASC,
              id DESC
            LIMIT 1
            """,
            (pdf_sha256,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_course_by_hash(self, *, chat_id: int, user_id: int, pdf_sha256: str) -> dict[str, Any] | None:
        cursor = await self.conn.execute(
            """
            SELECT * FROM courses WHERE chat_id=? AND user_id=? AND pdf_sha256=?
            ORDER BY id DESC LIMIT 1
            """,
            (chat_id, user_id, pdf_sha256),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def subscribe_user_to_course(self, *, chat_id: int, user_id: int, course_id: int) -> None:
        await self.conn.execute(
            """
            INSERT INTO course_subscribers(chat_id, user_id, course_id)
            VALUES (?, ?, ?)
            ON CONFLICT(chat_id, user_id, course_id) DO NOTHING
            """,
            (chat_id, user_id, course_id),
        )
        await self.conn.commit()

    async def set_selected_course(self, *, chat_id: int, user_id: int, course_id: int) -> None:
        await self.conn.execute(
            """
            INSERT INTO user_selected_course(chat_id, user_id, course_id, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(chat_id, user_id) DO UPDATE SET
              course_id=excluded.course_id,
              updated_at=CURRENT_TIMESTAMP
            """,
            (chat_id, user_id, course_id),
        )
        await self.conn.commit()

    async def get_selected_course(self, *, chat_id: int, user_id: int) -> dict[str, Any] | None:
        cursor = await self.conn.execute(
            """
            SELECT c.*
            FROM user_selected_course usc
            JOIN courses c ON c.id = usc.course_id
            WHERE usc.chat_id=? AND usc.user_id=? AND c.status='ready'
            LIMIT 1
            """,
            (chat_id, user_id),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_user_active_course(self, *, chat_id: int, user_id: int) -> dict[str, Any] | None:
        selected = await self.get_selected_course(chat_id=chat_id, user_id=user_id)
        if selected:
            return selected

        cursor = await self.conn.execute(
            """
            SELECT c.*
            FROM course_subscribers cs
            JOIN courses c ON c.id = cs.course_id
            WHERE cs.chat_id=? AND cs.user_id=? AND c.status='ready'
            ORDER BY cs.created_at DESC
            LIMIT 1
            """,
            (chat_id, user_id),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def list_ready_courses(self, *, limit: int = 20) -> list[dict[str, Any]]:
        cursor = await self.conn.execute(
            """
            SELECT
              c.*,
              (SELECT COUNT(*) FROM topic_nodes t WHERE t.course_id = c.id) AS topics_count,
              (SELECT COUNT(*) FROM question_bank q WHERE q.course_id = c.id) AS questions_count
            FROM courses c
            WHERE c.status='ready'
            ORDER BY c.updated_at DESC, c.id DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_course_by_id(self, course_id: int) -> dict[str, Any] | None:
        cursor = await self.conn.execute("SELECT * FROM courses WHERE id=?", (course_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_latest_ready_course(self, *, chat_id: int, user_id: int) -> dict[str, Any] | None:
        cursor = await self.conn.execute(
            """
            SELECT * FROM courses
            WHERE chat_id=? AND user_id=? AND status='ready'
            ORDER BY id DESC
            LIMIT 1
            """,
            (chat_id, user_id),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def update_course_status(
        self,
        *,
        course_id: int,
        status: str,
        compiled_markdown_path: str | None = None,
    ) -> None:
        await self.conn.execute(
            """
            UPDATE courses
            SET status=?, compiled_markdown_path=COALESCE(?, compiled_markdown_path), updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (status, compiled_markdown_path, course_id),
        )
        await self.conn.commit()

    async def update_course_pdf_path(self, *, course_id: int, pdf_path: str) -> None:
        await self.conn.execute(
            """
            UPDATE courses
            SET pdf_path=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (pdf_path, course_id),
        )
        await self.conn.commit()

    async def upsert_ocr_page(
        self,
        *,
        course_id: int,
        page_number: int,
        markdown_text: str,
        formula_text: str,
        raw_json_path: str,
    ) -> None:
        await self.conn.execute(
            """
            INSERT INTO ocr_pages(course_id, page_number, markdown_text, formula_text, raw_json_path)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(course_id, page_number) DO UPDATE SET
              markdown_text=excluded.markdown_text,
              formula_text=excluded.formula_text,
              raw_json_path=excluded.raw_json_path
            """,
            (course_id, page_number, markdown_text, formula_text, raw_json_path),
        )
        await self.conn.commit()

    async def list_ocr_pages(self, course_id: int) -> list[dict[str, Any]]:
        cursor = await self.conn.execute(
            "SELECT * FROM ocr_pages WHERE course_id=? ORDER BY page_number ASC", (course_id,)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def replace_theory_chunks(
        self,
        *,
        course_id: int,
        chunks: Iterable[dict[str, Any]],
    ) -> None:
        await self.conn.execute("DELETE FROM theory_chunks WHERE course_id=?", (course_id,))
        for chunk in chunks:
            await self.conn.execute(
                """
                INSERT INTO theory_chunks(course_id, chunk_index, text, start_page, end_page, token_estimate)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    course_id,
                    chunk["chunk_index"],
                    chunk["text"],
                    chunk["start_page"],
                    chunk["end_page"],
                    chunk["token_estimate"],
                ),
            )
        await self.conn.commit()

    async def list_theory_chunks(self, course_id: int) -> list[dict[str, Any]]:
        cursor = await self.conn.execute(
            "SELECT * FROM theory_chunks WHERE course_id=? ORDER BY chunk_index ASC", (course_id,)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def search_theory_chunks(self, *, course_id: int, query: str, limit: int = 6) -> list[SearchChunk]:
        sanitized = sanitize_fts_query(query)
        if not sanitized:
            return []

        try:
            cursor = await self.conn.execute(
                """
                SELECT tc.id, tc.text, tc.start_page, tc.end_page, bm25(theory_chunks_fts) AS rank
                FROM theory_chunks_fts
                JOIN theory_chunks tc ON tc.id = theory_chunks_fts.rowid
                WHERE theory_chunks_fts MATCH ? AND tc.course_id = ?
                ORDER BY rank ASC
                LIMIT ?
                """,
                (sanitized, course_id, limit),
            )
            rows = await cursor.fetchall()
        except aiosqlite.OperationalError:
            return []

        return [
            SearchChunk(
                id=row["id"],
                text=row["text"],
                start_page=row["start_page"],
                end_page=row["end_page"],
                rank=row["rank"],
            )
            for row in rows
        ]

    async def replace_topic_nodes(self, *, course_id: int, topics: list[TopicNode]) -> None:
        await self.conn.execute("DELETE FROM topic_nodes WHERE course_id=?", (course_id,))
        for topic in topics:
            await self.conn.execute(
                """
                INSERT INTO topic_nodes(
                    course_id, topic_key, title, summary,
                    definitions_json, formulas_json, related_json, prerequisites_json, difficulty
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    course_id,
                    topic.topic_key,
                    topic.title,
                    topic.summary,
                    json.dumps(topic.definitions, ensure_ascii=False),
                    json.dumps(topic.formulas, ensure_ascii=False),
                    json.dumps(topic.related, ensure_ascii=False),
                    json.dumps(topic.prerequisites, ensure_ascii=False),
                    topic.difficulty,
                ),
            )
        await self.conn.commit()

    async def list_topic_nodes(self, course_id: int) -> list[dict[str, Any]]:
        cursor = await self.conn.execute(
            "SELECT * FROM topic_nodes WHERE course_id=? ORDER BY topic_key ASC", (course_id,)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_topic_node(self, *, course_id: int, topic_key: str) -> dict[str, Any] | None:
        cursor = await self.conn.execute(
            "SELECT * FROM topic_nodes WHERE course_id=? AND topic_key=? LIMIT 1",
            (course_id, topic_key),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def replace_question_bank(self, *, course_id: int, questions: list[QuestionCard]) -> None:
        await self.conn.execute("DELETE FROM question_bank WHERE course_id=?", (course_id,))
        for item in questions:
            await self.conn.execute(
                """
                INSERT INTO question_bank(
                    course_id, topic_key, scope_type, difficulty,
                    question, ideal_answer, rubric_json, source_topic_keys_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    course_id,
                    item.topic_key,
                    item.scope_type,
                    item.difficulty,
                    item.question,
                    item.ideal_answer,
                    json.dumps(item.rubric, ensure_ascii=False),
                    json.dumps(item.source_topic_keys, ensure_ascii=False),
                ),
            )
        await self.conn.commit()

    async def count_question_bank(self, course_id: int) -> int:
        cursor = await self.conn.execute(
            "SELECT COUNT(*) AS cnt FROM question_bank WHERE course_id=?",
            (course_id,),
        )
        row = await cursor.fetchone()
        return int(row["cnt"]) if row else 0

    async def get_question_by_id(self, question_id: int) -> dict[str, Any] | None:
        cursor = await self.conn.execute("SELECT * FROM question_bank WHERE id=? LIMIT 1", (question_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_questions_by_ids(self, question_ids: list[int]) -> list[dict[str, Any]]:
        if not question_ids:
            return []
        placeholders = ",".join("?" for _ in question_ids)
        cursor = await self.conn.execute(
            f"SELECT * FROM question_bank WHERE id IN ({placeholders})",
            question_ids,
        )
        rows = [dict(row) for row in await cursor.fetchall()]
        by_id = {row["id"]: row for row in rows}
        return [by_id[qid] for qid in question_ids if qid in by_id]

    async def sample_questions(
        self,
        *,
        course_id: int,
        topic_keys: list[str],
        limit: int,
        scope_mix: dict[str, float],
        exclude_ids: set[int] | None = None,
    ) -> list[dict[str, Any]]:
        if not topic_keys:
            return []

        exclude_ids = exclude_ids or set()
        placeholders = ",".join("?" for _ in topic_keys)
        cursor = await self.conn.execute(
            f"""
            SELECT * FROM question_bank
            WHERE course_id=? AND topic_key IN ({placeholders})
            """,
            [course_id, *topic_keys],
        )
        rows = [dict(row) for row in await cursor.fetchall()]

        if exclude_ids:
            rows = [row for row in rows if row["id"] not in exclude_ids]

        if not rows:
            return []

        by_scope: dict[str, list[dict[str, Any]]] = {"core": [], "deep": [], "beyond": []}
        for row in rows:
            by_scope.setdefault(row["scope_type"], []).append(row)

        selected: list[dict[str, Any]] = []
        for scope, fraction in scope_mix.items():
            scope_limit = max(1, int(limit * fraction))
            candidates = by_scope.get(scope, [])
            random.shuffle(candidates)
            selected.extend(candidates[:scope_limit])

        if len(selected) < limit:
            leftovers = [r for r in rows if r not in selected]
            random.shuffle(leftovers)
            selected.extend(leftovers[: max(0, limit - len(selected))])

        random.shuffle(selected)
        return selected[:limit]

    async def create_exam_session(
        self,
        *,
        chat_id: int,
        user_id: int,
        course_id: int,
        current_topic_key: str,
        question_plan: list[int],
        current_question: dict[str, Any] | None,
        covered_topics: list[str],
    ) -> dict[str, Any]:
        cursor = await self.conn.execute(
            """
            INSERT INTO exam_sessions(
                chat_id, user_id, course_id, status, current_topic_key,
                question_plan_json, current_question_json, covered_topics_json
            )
            VALUES (?, ?, ?, 'active', ?, ?, ?, ?)
            """,
            (
                chat_id,
                user_id,
                course_id,
                current_topic_key,
                json.dumps(question_plan, ensure_ascii=False),
                json.dumps(current_question, ensure_ascii=False) if current_question else None,
                json.dumps(covered_topics, ensure_ascii=False),
            ),
        )
        await self.conn.commit()
        session_id = cursor.lastrowid
        return await self.get_exam_session_by_id(session_id)

    async def get_exam_session_by_id(self, session_id: int) -> dict[str, Any]:
        cursor = await self.conn.execute("SELECT * FROM exam_sessions WHERE id=?", (session_id,))
        row = await cursor.fetchone()
        if not row:
            raise RuntimeError(f"Session not found: {session_id}")
        return dict(row)

    async def get_active_exam_session(self, *, chat_id: int, user_id: int) -> dict[str, Any] | None:
        cursor = await self.conn.execute(
            """
            SELECT * FROM exam_sessions
            WHERE chat_id=? AND user_id=? AND status='active'
            ORDER BY id DESC
            LIMIT 1
            """,
            (chat_id, user_id),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def update_exam_session(
        self,
        *,
        session_id: int,
        score_total: float,
        score_count: int,
        asked_count: int,
        session_summary: str,
        current_topic_key: str | None,
        question_plan: list[int],
        current_question: dict[str, Any] | None,
        covered_topics: list[str],
    ) -> None:
        await self.conn.execute(
            """
            UPDATE exam_sessions
            SET
              score_total=?,
              score_count=?,
              asked_count=?,
              session_summary=?,
              current_topic_key=?,
              question_plan_json=?,
              current_question_json=?,
              covered_topics_json=?,
              updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (
                score_total,
                score_count,
                asked_count,
                session_summary,
                current_topic_key,
                json.dumps(question_plan, ensure_ascii=False),
                json.dumps(current_question, ensure_ascii=False) if current_question else None,
                json.dumps(covered_topics, ensure_ascii=False),
                session_id,
            ),
        )
        await self.conn.commit()

    async def finish_exam_session(self, *, session_id: int) -> dict[str, Any]:
        await self.conn.execute(
            """
            UPDATE exam_sessions
            SET status='finished', ended_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (session_id,),
        )
        await self.conn.commit()
        return await self.get_exam_session_by_id(session_id)

    async def add_exam_turn(
        self,
        *,
        session_id: int,
        question_id: int | None,
        question_text: str,
        expected_answer: str,
        student_answer: str,
        score: float,
        max_score: float,
        feedback: str,
        topic_key: str,
        scope_type: str,
    ) -> None:
        await self.conn.execute(
            """
            INSERT INTO exam_turns(
                session_id, question_id, question_text, expected_answer,
                student_answer, score, max_score, feedback, topic_key, scope_type
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                question_id,
                question_text,
                expected_answer,
                student_answer,
                score,
                max_score,
                feedback,
                topic_key,
                scope_type,
            ),
        )
        await self.conn.commit()

    async def upsert_user_topic_stat(
        self,
        *,
        user_id: int,
        course_id: int,
        topic_key: str,
        score: float,
    ) -> None:
        current = await self.get_user_topic_stat(user_id=user_id, course_id=course_id, topic_key=topic_key)
        if current:
            attempts = int(current["attempts"]) + 1
            avg = (float(current["avg_score"]) * int(current["attempts"]) + score) / attempts
            await self.conn.execute(
                """
                UPDATE user_topic_stats
                SET attempts=?, avg_score=?, last_score=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (attempts, avg, score, current["id"]),
            )
        else:
            await self.conn.execute(
                """
                INSERT INTO user_topic_stats(user_id, course_id, topic_key, attempts, avg_score, last_score)
                VALUES (?, ?, ?, 1, ?, ?)
                """,
                (user_id, course_id, topic_key, score, score),
            )
        await self.conn.commit()

    async def get_user_topic_stat(
        self,
        *,
        user_id: int,
        course_id: int,
        topic_key: str,
    ) -> dict[str, Any] | None:
        cursor = await self.conn.execute(
            """
            SELECT * FROM user_topic_stats
            WHERE user_id=? AND course_id=? AND topic_key=?
            LIMIT 1
            """,
            (user_id, course_id, topic_key),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_user_topic_stats_map(self, *, user_id: int, course_id: int) -> dict[str, dict[str, Any]]:
        cursor = await self.conn.execute(
            "SELECT * FROM user_topic_stats WHERE user_id=? AND course_id=?",
            (user_id, course_id),
        )
        rows = [dict(row) for row in await cursor.fetchall()]
        return {row["topic_key"]: row for row in rows}

    async def get_user_stats_summary(self, *, chat_id: int, user_id: int) -> dict[str, Any]:
        cursor = await self.conn.execute(
            """
            SELECT
              COUNT(*) AS sessions_count,
              COALESCE(AVG(CASE WHEN score_count > 0 THEN score_total / score_count END), 0) AS avg_session_score,
              MAX(updated_at) AS last_activity
            FROM exam_sessions
            WHERE chat_id=? AND user_id=? AND status='finished'
            """,
            (chat_id, user_id),
        )
        row = await cursor.fetchone()
        return dict(row) if row else {"sessions_count": 0, "avg_session_score": 0, "last_activity": None}

    async def list_recent_turns(self, *, session_id: int, limit: int = 6) -> list[dict[str, Any]]:
        cursor = await self.conn.execute(
            """
            SELECT * FROM exam_turns
            WHERE session_id=?
            ORDER BY id DESC
            LIMIT ?
            """,
            (session_id, limit),
        )
        rows = await cursor.fetchall()
        result = [dict(row) for row in rows]
        result.reverse()
        return result

    async def list_session_question_ids(self, *, session_id: int) -> set[int]:
        cursor = await self.conn.execute(
            "SELECT question_id FROM exam_turns WHERE session_id=? AND question_id IS NOT NULL",
            (session_id,),
        )
        rows = await cursor.fetchall()
        return {int(row["question_id"]) for row in rows}
