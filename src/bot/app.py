from __future__ import annotations

import logging

from telegram import BotCommand, Update
from telegram.ext import Application, ApplicationBuilder, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from src.bot.comsa import load_comsa_questions
from src.bot.course_service import CourseService
from src.bot.handlers import (
    HandlerDeps,
    cmd_comsa_mode,
    cmd_exam_start,
    cmd_exam_stop,
    cmd_help,
    cmd_courses,
    cmd_show_answers,
    cmd_start,
    cmd_stats,
    cmd_use_course,
    cmd_upload_theory,
    on_course_select_callback,
    on_pdf_document,
    on_text_message,
)
from src.config.settings import Settings
from src.exam.engine import ExamEngine
from src.knowledge.builder import KnowledgeBuilder
from src.knowledge.retrieval import KnowledgeRetrieval
from src.llm.gateway import DashScopeChatClient
from src.ocr.ingestion import OcrIngestionService
from src.ocr.qwen_ocr import QwenOcrClient
from src.storage.db import Database

logger = logging.getLogger(__name__)


async def _post_init_application(application: Application) -> None:
    commands = [
        BotCommand("start", "Старт и краткая инструкция"),
        BotCommand("help", "Список команд"),
        BotCommand("upload_theory", "Загрузить PDF теормина"),
        BotCommand("courses", "Показать готовые курсы"),
        BotCommand("use_course", "Выбрать курс по ID"),
        BotCommand("exam_start", "Начать экзамен"),
        BotCommand("exam_stop", "Завершить экзамен"),
        BotCommand("stats", "Показать статистику"),
        BotCommand("comsa_mode", "Вкл/выкл режим COMSA"),
        BotCommand("show_answers", "Ответы на вопросы COMSA"),
    ]
    try:
        await application.bot.set_my_commands(commands)
        logger.info("Telegram commands menu updated")
    except Exception:
        logger.exception("Failed to set Telegram commands menu")


async def _shutdown_application(application: Application) -> None:
    deps: HandlerDeps = application.bot_data.get("deps")
    llm: DashScopeChatClient = application.bot_data.get("llm_client")
    ocr: QwenOcrClient = application.bot_data.get("ocr_client")
    db: Database = application.bot_data.get("db")

    if llm:
        await llm.close()
    if ocr:
        await ocr.close()
    if db:
        await db.close()


async def create_application(settings: Settings) -> Application:
    db = Database(str(settings.db_path))
    await db.connect()
    await db.init_schema()

    llm_client = DashScopeChatClient(settings)
    ocr_client = QwenOcrClient(settings)

    ingestion = OcrIngestionService(db=db, ocr_client=ocr_client)
    knowledge_builder = KnowledgeBuilder(db=db, llm=llm_client)
    course_service = CourseService(
        db=db,
        ingestion=ingestion,
        knowledge_builder=knowledge_builder,
        courses_root=settings.courses_dir,
    )

    retrieval = KnowledgeRetrieval(db)
    exam_engine = ExamEngine(db=db, llm=llm_client, retrieval=retrieval, settings=settings)

    comsa_path = settings.data_dir / "comsa_questions.md"
    comsa_questions: list[str] = []
    if comsa_path.exists():
        comsa_questions = load_comsa_questions(comsa_path)
        logger.info("Loaded %d COMSA questions from %s", len(comsa_questions), comsa_path)
    else:
        logger.warning("COMSA questions file not found at %s", comsa_path)

    deps = HandlerDeps(
        settings=settings,
        db=db,
        course_service=course_service,
        exam_engine=exam_engine,
        retrieval=retrieval,
        llm_client=llm_client,
        comsa_questions=comsa_questions,
    )

    application = (
        ApplicationBuilder()
        .token(settings.telegram_bot_token)
        .post_init(_post_init_application)
        .post_shutdown(_shutdown_application)
        .build()
    )

    application.bot_data["deps"] = deps
    application.bot_data["llm_client"] = llm_client
    application.bot_data["ocr_client"] = ocr_client
    application.bot_data["db"] = db

    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("upload_theory", cmd_upload_theory))
    application.add_handler(CommandHandler("courses", cmd_courses))
    application.add_handler(CommandHandler("use_course", cmd_use_course))
    application.add_handler(CommandHandler("exam_start", cmd_exam_start))
    application.add_handler(CommandHandler("exam_stop", cmd_exam_stop))
    application.add_handler(CommandHandler("stats", cmd_stats))
    application.add_handler(CommandHandler("comsa_mode", cmd_comsa_mode))
    application.add_handler(CommandHandler("show_answers", cmd_show_answers))
    application.add_handler(CallbackQueryHandler(on_course_select_callback, pattern=r"^select_course:"))

    application.add_handler(MessageHandler(filters.Document.PDF, on_pdf_document))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text_message))

    return application


def run_bot(settings: Settings) -> None:
    import asyncio

    app = asyncio.run(create_application(settings))
    try:
        app.run_polling(allowed_updates=Update.ALL_TYPES)
    finally:
        asyncio.run(_shutdown_application(app))
