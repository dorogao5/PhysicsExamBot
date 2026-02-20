from __future__ import annotations

import asyncio
import logging
from uuid import uuid4

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from src.bot.course_service import CourseService
from src.bot.text_format import markdownish_to_telegram_html
from src.config.settings import Settings
from src.exam.engine import ExamEngine
from src.storage.db import Database

logger = logging.getLogger(__name__)

UPLOAD_LOCKS: dict[tuple[int, int], asyncio.Lock] = {}


class HandlerDeps:
    def __init__(self, *, settings: Settings, db: Database, course_service: CourseService, exam_engine: ExamEngine):
        self.settings = settings
        self.db = db
        self.course_service = course_service
        self.exam_engine = exam_engine


def get_deps(context: ContextTypes.DEFAULT_TYPE) -> HandlerDeps:
    deps = context.application.bot_data.get("deps")
    if not isinstance(deps, HandlerDeps):
        raise RuntimeError("Handler dependencies are not initialized")
    return deps


async def _reply(update: Update, text: str, **kwargs) -> None:
    message = update.message
    if not message:
        return
    rendered = markdownish_to_telegram_html(text)
    try:
        await message.reply_text(rendered, parse_mode=ParseMode.HTML, **kwargs)
    except BadRequest:
        await message.reply_text(text, **kwargs)


async def _edit(status_message, text: str, **kwargs) -> None:
    rendered = markdownish_to_telegram_html(text)
    try:
        await status_message.edit_text(rendered, parse_mode=ParseMode.HTML, **kwargs)
    except BadRequest:
        await status_message.edit_text(text, **kwargs)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply(
        update,
        "Бот экзаменатора по физике готов.\n"
        "1) /upload_theory\n"
        "2) Или выберите уже готовый курс: /courses\n"
        "3) /exam_start",
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply(
        update,
        "Команды:\n"
        "/upload_theory - загрузить PDF теормина\n"
        "/courses - список готовых курсов\n"
        "/use_course <id> - выбрать курс\n"
        "/exam_start - начать сдачу\n"
        "/exam_stop - завершить сдачу\n"
        "/stats - показать статистику",
    )


async def cmd_upload_theory(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply(update, "Пришлите PDF-файл теоретического минимума документом.")


async def cmd_courses(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    deps = get_deps(context)
    courses = await deps.db.list_ready_courses(limit=20)
    if not courses:
        await _reply(update, "Готовых курсов пока нет. Загрузите файл через /upload_theory")
        return

    lines: list[str] = ["Готовые курсы:"]
    keyboard_rows: list[list[InlineKeyboardButton]] = []
    for row in courses:
        course_id = int(row["id"])
        title = str(row.get("title") or "theory.pdf")
        title_short = (title[:42] + "...") if len(title) > 45 else title
        hash_short = str(row.get("pdf_sha256", ""))[:8]
        topics = int(row.get("topics_count") or 0)
        questions = int(row.get("questions_count") or 0)

        lines.append(f"#{course_id} | {title_short} | hash {hash_short} | тем {topics} | вопр {questions}")
        keyboard_rows.append([InlineKeyboardButton(f"Выбрать #{course_id}", callback_data=f"select_course:{course_id}")])

    text = "\n".join(lines)
    rendered = markdownish_to_telegram_html(text)
    try:
        await update.message.reply_text(rendered, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard_rows))
    except BadRequest:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard_rows))


async def cmd_use_course(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    deps = get_deps(context)
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat:
        return

    if not context.args:
        await _reply(update, "Использование: /use_course <id>")
        return

    try:
        course_id = int(context.args[0])
    except ValueError:
        await _reply(update, "ID курса должен быть целым числом.")
        return

    course = await deps.db.get_course_by_id(course_id)
    if not course or course.get("status") != "ready":
        await _reply(update, "Курс не найден или еще не готов.")
        return

    await deps.db.subscribe_user_to_course(chat_id=chat.id, user_id=user.id, course_id=course_id)
    await deps.db.set_selected_course(chat_id=chat.id, user_id=user.id, course_id=course_id)
    await _reply(update, f"Выбран курс #{course_id}. Теперь можно /exam_start")


async def on_course_select_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    deps = get_deps(context)

    query = update.callback_query
    user = update.effective_user
    chat = update.effective_chat
    if not query or not user or not chat:
        return

    data = query.data or ""
    if not data.startswith("select_course:"):
        await query.answer()
        return

    try:
        course_id = int(data.split(":", 1)[1])
    except ValueError:
        await query.answer("Некорректный ID", show_alert=True)
        return

    course = await deps.db.get_course_by_id(course_id)
    if not course or course.get("status") != "ready":
        await query.answer("Курс не готов", show_alert=True)
        return

    await deps.db.subscribe_user_to_course(chat_id=chat.id, user_id=user.id, course_id=course_id)
    await deps.db.set_selected_course(chat_id=chat.id, user_id=user.id, course_id=course_id)

    await query.answer("Курс выбран")
    rendered = markdownish_to_telegram_html(f"Выбран курс #{course_id}. Запускайте /exam_start")
    try:
        await query.edit_message_text(rendered, parse_mode=ParseMode.HTML)
    except BadRequest:
        await query.edit_message_text(f"Выбран курс #{course_id}. Запускайте /exam_start")


async def on_pdf_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    deps = get_deps(context)

    if not update.message or not update.message.document:
        return

    document = update.message.document
    if document.mime_type != "application/pdf":
        await _reply(update, "Нужен именно PDF (`application/pdf`).")
        return

    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat:
        return

    lock_key = (chat.id, user.id)
    lock = UPLOAD_LOCKS.setdefault(lock_key, asyncio.Lock())

    if lock.locked():
        await _reply(update, "Предыдущая обработка PDF еще идет, дождитесь завершения.")
        return

    async with lock:
        file_obj = await document.get_file()
        tmp_path = deps.settings.tmp_dir / f"upload_{uuid4().hex}.pdf"
        await file_obj.download_to_drive(custom_path=str(tmp_path))

        status_message = await update.message.reply_text("PDF получен, запускаю обработку...")

        async def progress(text: str) -> None:
            try:
                await _edit(status_message, text)
            except Exception:
                logger.debug("Status edit failed", exc_info=True)

        try:
            result = await deps.course_service.process_uploaded_pdf(
                chat_id=chat.id,
                user_id=user.id,
                title=document.file_name or "theory.pdf",
                uploaded_path=tmp_path,
                on_progress=progress,
            )
            if result.get("processing"):
                await _edit(
                    status_message,
                    f"Курс #{result['course_id']} уже обрабатывается другим запросом. "
                    "Дождитесь завершения и используйте /exam_start или /courses.",
                )
            elif result.get("reused"):
                await _edit(
                    status_message,
                    f"Этот PDF уже обработан. Выбран курс #{result['course_id']}. Готово к сдаче: /exam_start",
                )
            else:
                await _edit(
                    status_message,
                    "Готово. "
                    f"Курс #{result['course_id']}\n"
                    f"Тем: {result['topics_count']}\n"
                    f"Вопросов: {result['questions_count']}\n"
                    "Запускайте /exam_start",
                )
        except Exception as exc:
            logger.exception("Course processing failed")
            text = str(exc)
            if "No available OCR model" in text or "Model not exist" in text:
                await _edit(
                    status_message,
                    "OCR-модель недоступна для вашего ключа/региона. "
                    "Проверьте QWEN_OCR_MODEL (рекомендуется qwen-vl-ocr) и доступ к модели в DashScope.",
                )
            else:
                await _edit(status_message, f"Ошибка при обработке PDF: {exc}")
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                logger.debug("Temp file remove failed", exc_info=True)


async def cmd_exam_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    deps = get_deps(context)
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat:
        return

    course = await deps.db.get_user_active_course(chat_id=chat.id, user_id=user.id)
    if not course:
        await _reply(
            update,
            "Нет выбранного готового курса. Загрузите PDF (/upload_theory) или выберите курс (/courses)."
        )
        return

    text = await deps.exam_engine.start_exam(chat_id=chat.id, user_id=user.id, course_id=int(course["id"]))
    await _reply(update, text)


async def cmd_exam_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    deps = get_deps(context)
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat:
        return

    text = await deps.exam_engine.stop_exam(chat_id=chat.id, user_id=user.id)
    await _reply(update, text)


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    deps = get_deps(context)
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat:
        return

    stats = await deps.db.get_user_stats_summary(chat_id=chat.id, user_id=user.id)
    await _reply(
        update,
        "Статистика:\n"
        f"Сданных сессий: {int(stats['sessions_count'])}\n"
        f"Средний балл: {float(stats['avg_session_score']):.1f}\n"
        f"Последняя активность: {stats['last_activity'] or '-'}",
    )


async def on_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    deps = get_deps(context)

    user = update.effective_user
    chat = update.effective_chat
    message = update.message
    if not user or not chat or not message or not message.text:
        return

    reply = await deps.exam_engine.handle_answer(chat_id=chat.id, user_id=user.id, student_answer=message.text)
    if reply:
        await _reply(update, reply)
