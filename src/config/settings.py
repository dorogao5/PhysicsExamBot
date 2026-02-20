from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    telegram_bot_token: str = Field(alias="TELEGRAM_BOT_TOKEN")
    dashscope_api_key: str = Field(alias="DASHSCOPE_API_KEY")

    dashscope_base_url: str = Field(
        default="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        alias="DASHSCOPE_BASE_URL",
    )
    dashscope_aigc_base_url: str = Field(
        default="https://dashscope-intl.aliyuncs.com/api/v1",
        alias="DASHSCOPE_AIGC_BASE_URL",
    )

    qwen_chat_model: str = Field(default="qwen3-max", alias="QWEN_CHAT_MODEL")
    qwen_ocr_model: str = Field(default="qwen-vl-ocr", alias="QWEN_OCR_MODEL")

    admin_user_ids_raw: str = Field(default="", alias="ADMIN_USER_IDS")

    default_language: str = Field(default="ru", alias="DEFAULT_LANGUAGE")
    pass_threshold: int = Field(default=70, alias="PASS_THRESHOLD")
    min_questions_for_pass: int = Field(default=10, alias="MIN_QUESTIONS_FOR_PASS")

    top_k_retrieval: int = Field(default=6, alias="TOP_K_RETRIEVAL")
    max_context_chars: int = Field(default=3500, alias="MAX_CONTEXT_CHARS")

    data_dir: Path = Field(default=Path("data"), alias="DATA_DIR")
    courses_dir: Path = Field(default=Path("data/courses"), alias="COURSES_DIR")
    db_path: Path = Field(default=Path("data/bot.sqlite3"), alias="DB_PATH")
    tmp_dir: Path = Field(default=Path("data/tmp"), alias="TMP_DIR")

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def admin_user_ids(self) -> List[int]:
        if not self.admin_user_ids_raw.strip():
            return []
        values: List[int] = []
        for item in self.admin_user_ids_raw.split(","):
            item = item.strip()
            if not item:
                continue
            values.append(int(item))
        return values

    @model_validator(mode="after")
    def _resolve_runtime_paths(self) -> "Settings":
        self.data_dir = self.data_dir.expanduser()
        if "courses_dir" not in self.model_fields_set:
            self.courses_dir = self.data_dir / "courses"
        else:
            self.courses_dir = self.courses_dir.expanduser()

        if "db_path" not in self.model_fields_set:
            self.db_path = self.data_dir / "bot.sqlite3"
        else:
            self.db_path = self.db_path.expanduser()

        if "tmp_dir" not in self.model_fields_set:
            self.tmp_dir = self.data_dir / "tmp"
        else:
            self.tmp_dir = self.tmp_dir.expanduser()
        return self

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.courses_dir.mkdir(parents=True, exist_ok=True)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_directories()
    return settings
