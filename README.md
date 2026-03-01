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
- **COMSA mode**: get 15 random exam questions with LLM-generated answers based on theory.

## Requirements

- Python >= 3.11
- Telegram bot token (from [@BotFather](https://t.me/BotFather))
- DashScope API key (Alibaba Cloud)

## Setup

```bash
git clone <repo-url>
cd PhysicsExamBot

python3 -m venv .venv
source .venv/bin/activate

# install runtime dependencies (editable mode)
pip install -e .

# install dev dependencies (pytest)
pip install -e '.[dev]'

# configure environment
cp .env.example .env
# fill TELEGRAM_BOT_TOKEN and DASHSCOPE_API_KEY in .env
```

## Running locally

```bash
source .venv/bin/activate
python main.py
```

The bot uses long-polling, no webhooks or external ports required.

## Running as a systemd service (24/7)

A service file is installed at `/etc/systemd/system/physics-exam-bot.service`.

```bash
# reload after any changes to the service file
sudo systemctl daemon-reload

# enable auto-start on boot
sudo systemctl enable physics-exam-bot

# start / stop / restart
sudo systemctl start physics-exam-bot
sudo systemctl stop physics-exam-bot
sudo systemctl restart physics-exam-bot

# check status and recent logs
sudo systemctl status physics-exam-bot
sudo journalctl -u physics-exam-bot -f          # follow live logs
sudo journalctl -u physics-exam-bot --since today  # today's logs
```

After editing code, apply changes with:

```bash
sudo systemctl restart physics-exam-bot
```

## Tests

```bash
source .venv/bin/activate
python -m pytest tests/ -v
```

## Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome message and quick instructions |
| `/help` | List all commands |
| `/upload_theory` | Upload a PDF theory minimum |
| `/courses` | Show processed courses |
| `/use_course <id>` | Select a course by ID |
| `/exam_start` | Start oral exam (or COMSA questions if in COMSA mode) |
| `/exam_stop` | End current exam session |
| `/stats` | Show your exam statistics |
| `/comsa_mode` | Toggle COMSA mode on/off |
| `/show_answers` | Show LLM-generated answers for active COMSA questions |

## COMSA mode

1. `/comsa_mode` — enable COMSA mode.
2. `/exam_start` — receive 15 random questions from `data/comsa_questions.md`.
3. `/show_answers` — get answers generated from `data/courses/1/theory_compiled.md` via LLM.
4. `/comsa_mode` — disable COMSA mode and return to regular exams.

## Configuration

All settings are loaded from `.env` (see `.env.example`):

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | yes | — | Bot token from BotFather |
| `DASHSCOPE_API_KEY` | yes | — | Alibaba DashScope API key |
| `QWEN_CHAT_MODEL` | no | `qwen3-max` | Chat/grading model |
| `QWEN_OCR_MODEL` | no | `qwen-vl-ocr` | OCR model |
| `DATA_DIR` | no | `data` | Root data directory |
| `PASS_THRESHOLD` | no | `70` | Minimum score to pass (0-100) |
| `MIN_QUESTIONS_FOR_PASS` | no | `10` | Minimum questions for a valid exam |

## Data layout

```
data/
├── bot.sqlite3                          # SQLite database
├── comsa_questions.md                   # COMSA question bank (119 questions)
├── tmp/                                 # temporary uploads
└── courses/
    └── <course_id>/
        ├── theory.pdf                   # original PDF
        ├── theory_compiled.md           # compiled OCR text
        └── ocr/pages/page_XXXX.json    # per-page OCR output
```

## Production deploy

1. Set `DATA_DIR=/var/lib/physics_bot` in `.env` for persistent storage outside the code directory.
2. Copy existing data: `cp -a data/. /var/lib/physics_bot/`
3. Run only **one polling instance** per bot token.
4. Do not delete `DB_PATH` or `COURSES_DIR` between deploys.

## API references

- [Alibaba Model Studio OpenAI-compatible Chat](https://www.alibabacloud.com/help/en/model-studio/developer-reference/text-generation)
- [Qwen OCR API reference](https://www.alibabacloud.com/help/en/model-studio/qwen-ocr-api-reference)
- [Qwen OCR usage guide](https://www.alibabacloud.com/help/en/model-studio/use-qwen-ocr-by-calling-api)
- [Structured output](https://www.alibabacloud.com/help/en/model-studio/user-guide/structured-output)
- [Context cache](https://www.alibabacloud.com/help/en/model-studio/context-cache)
