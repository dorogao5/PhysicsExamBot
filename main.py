from __future__ import annotations

from src.bot.app import run_bot
from src.config.settings import get_settings
from src.utils.logging import setup_logging


def main() -> None:
    setup_logging()
    settings = get_settings()
    run_bot(settings)


if __name__ == "__main__":
    main()
