from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class Course(BaseModel):
    id: int
    chat_id: int
    user_id: int
    title: str
    pdf_sha256: str
    pdf_path: str
    compiled_markdown_path: str | None = None
    status: str
    created_at: datetime
    updated_at: datetime


class OcrPage(BaseModel):
    id: int | None = None
    course_id: int
    page_number: int
    markdown_text: str
    formula_text: str | None = None
    raw_json_path: str


class TheoryChunk(BaseModel):
    id: int | None = None
    course_id: int
    chunk_index: int
    text: str
    start_page: int
    end_page: int
    token_estimate: int


class TopicNode(BaseModel):
    id: int | None = None
    course_id: int
    topic_key: str
    title: str
    summary: str
    definitions: list[str] = Field(default_factory=list)
    formulas: list[str] = Field(default_factory=list)
    related: list[str] = Field(default_factory=list)
    prerequisites: list[str] = Field(default_factory=list)
    difficulty: int = 1


class QuestionCard(BaseModel):
    id: int | None = None
    course_id: int
    topic_key: str
    scope_type: Literal["core", "deep", "beyond"]
    difficulty: int = 1
    question: str
    ideal_answer: str
    rubric: list[str] = Field(default_factory=list)
    source_topic_keys: list[str] = Field(default_factory=list)


class TurnAssessment(BaseModel):
    score: float = Field(ge=0, le=100)
    max_score: float = 100
    verdict: Literal["correct", "partial", "incorrect"]
    missing_points: list[str] = Field(default_factory=list)
    feedback_short: str


class ExamSession(BaseModel):
    id: int
    chat_id: int
    user_id: int
    course_id: int
    status: Literal["active", "finished"]
    score_total: float = 0
    score_count: int = 0
    asked_count: int = 0
    session_summary: str = ""
    current_topic_key: str | None = None
    question_plan: list[int] = Field(default_factory=list)
    current_question: dict | None = None
    covered_topics: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class ExamTurn(BaseModel):
    id: int | None = None
    session_id: int
    question_id: int | None = None
    question_text: str
    expected_answer: str
    student_answer: str
    score: float
    max_score: float
    feedback: str
    topic_key: str
    scope_type: str


class TopicExtractionResult(BaseModel):
    topics: list[TopicNode] = Field(default_factory=list)


class QuestionGenerationResult(BaseModel):
    questions: list[QuestionCard] = Field(default_factory=list)
