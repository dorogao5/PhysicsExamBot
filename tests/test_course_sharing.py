import pytest

from src.storage.db import Database


@pytest.mark.asyncio
async def test_course_is_shared_across_users_and_selectable(tmp_path) -> None:
    db = Database(str(tmp_path / "test.sqlite3"))
    await db.connect()
    await db.init_schema()

    course1, created1 = await db.create_or_get_course(
        chat_id=11,
        user_id=101,
        title="electro.pdf",
        pdf_sha256="samehash",
        pdf_path="/tmp/a.pdf",
    )
    assert created1 is True

    course2, created2 = await db.create_or_get_course(
        chat_id=12,
        user_id=202,
        title="other-name.pdf",
        pdf_sha256="samehash",
        pdf_path="/tmp/b.pdf",
    )
    assert created2 is False
    assert int(course1["id"]) == int(course2["id"])

    course_id = int(course1["id"])
    await db.update_course_status(course_id=course_id, status="ready")

    await db.subscribe_user_to_course(chat_id=12, user_id=202, course_id=course_id)
    await db.set_selected_course(chat_id=12, user_id=202, course_id=course_id)

    selected = await db.get_selected_course(chat_id=12, user_id=202)
    assert selected is not None
    assert int(selected["id"]) == course_id

    active = await db.get_user_active_course(chat_id=12, user_id=202)
    assert active is not None
    assert int(active["id"]) == course_id

    ready = await db.list_ready_courses(limit=10)
    assert any(int(row["id"]) == course_id for row in ready)

    await db.close()
