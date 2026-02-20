# Physics Examiner Bot

Telegram bot for physics oral exams based on:
- `qwen3-max` for exam logic and grading
- `qwen-vl-ocr` (with fallback chain) for OCR from theory minimum PDFs

## Features
- Upload a PDF with theory minimum via Telegram.
- Render each page to image and run OCR with formula-aware extraction.
- Resume OCR after interruption by reusing already processed pages from DB.
- Build a searchable local knowledge base (SQLite + FTS5).
- Generate topic graph and question bank.
- Start long oral exam sessions with adaptive follow-up questions.
- Grade each student answer with rubric-based structured output.
- Share processed courses across users: one OCR pass can be reused by everyone.

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
# fill TELEGRAM_BOT_TOKEN and DASHSCOPE_API_KEY
# optional for production persistence:
# DATA_DIR=/var/lib/physics_bot
python main.py
```

## Commands
- `/start`
- `/upload_theory`
- `/courses`
- `/use_course <id>`
- Send PDF document
- `/exam_start`
- `/exam_stop`
- `/stats`
- `/help`

## Data layout (default)
- `data/courses/<course_id>/theory.pdf`
- `data/courses/<course_id>/ocr/pages/page_XXXX.json`
- `data/courses/<course_id>/theory_compiled.md`
- SQLite database file: `data/bot.sqlite3`

## Safe deploy (no data loss)
1. Keep runtime data outside the code directory:
   - Set `DATA_DIR=/var/lib/physics_bot` in `.env`.
   - Optional fine-tuning: `COURSES_DIR`, `DB_PATH`, `TMP_DIR`.
2. Before first migration, copy existing local data:
   - `mkdir -p /var/lib/physics_bot`
   - `cp -a data/. /var/lib/physics_bot/`
3. Run only one polling instance per bot token. Two processes with the same token will conflict.
4. Do not delete `DB_PATH` and `COURSES_DIR` between deploys.

## Notes
- Uses Alibaba Cloud international endpoint by default.
- Designed for local long-polling mode.
- All sessions are isolated by `chat_id` and `user_id`.

## API references used
- [Alibaba Model Studio OpenAI-compatible Chat](https://www.alibabacloud.com/help/en/model-studio/developer-reference/text-generation)
- [Qwen OCR API reference](https://www.alibabacloud.com/help/en/model-studio/qwen-ocr-api-reference)
- [Qwen OCR usage guide](https://www.alibabacloud.com/help/en/model-studio/use-qwen-ocr-by-calling-api)
- [Structured output](https://www.alibabacloud.com/help/en/model-studio/user-guide/structured-output)
- [Context cache](https://www.alibabacloud.com/help/en/model-studio/context-cache)
