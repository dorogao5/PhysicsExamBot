import pytest

from src.knowledge.retrieval import KnowledgeRetrieval
from src.models.schemas import TopicNode
from src.storage.db import Database


@pytest.mark.asyncio
async def test_db_fts_and_related_topics(tmp_path) -> None:
    db_path = tmp_path / "test.sqlite3"
    db = Database(str(db_path))
    await db.connect()
    await db.init_schema()

    course, _ = await db.create_or_get_course(
        chat_id=1,
        user_id=2,
        title="theory.pdf",
        pdf_sha256="abc",
        pdf_path="/tmp/theory.pdf",
    )
    course_id = int(course["id"])

    await db.replace_theory_chunks(
        course_id=course_id,
        chunks=[
            {
                "chunk_index": 0,
                "text": "Уравнения Максвелла в вакууме и ток смещения",
                "start_page": 1,
                "end_page": 1,
                "token_estimate": 15,
            },
            {
                "chunk_index": 1,
                "text": "Теорема непрерывности и плотность тока",
                "start_page": 2,
                "end_page": 2,
                "token_estimate": 14,
            },
        ],
    )

    results = await db.search_theory_chunks(course_id=course_id, query="ток смещения", limit=3)
    assert results

    await db.replace_topic_nodes(
        course_id=course_id,
        topics=[
            TopicNode(
                course_id=course_id,
                topic_key="maxwell",
                title="Максвелл",
                summary="",
                definitions=[],
                formulas=[],
                related=["continuity"],
                prerequisites=[],
            ),
            TopicNode(
                course_id=course_id,
                topic_key="continuity",
                title="Непрерывность",
                summary="",
                definitions=[],
                formulas=[],
                related=[],
                prerequisites=[],
            ),
        ],
    )

    retrieval = KnowledgeRetrieval(db)
    related = await retrieval.get_related_topics(course_id=course_id, topic_key="maxwell", depth=1)
    assert "maxwell" in related
    assert "continuity" in related

    await db.close()
