from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.config.settings import Settings

logger = logging.getLogger(__name__)


class QwenOcrRetryableError(RuntimeError):
    pass


class QwenOcrFatalError(RuntimeError):
    pass


class QwenOcrClient:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=20.0))
        self._resolved_model: str | None = None

    async def close(self) -> None:
        await self._client.aclose()

    @retry(
        retry=retry_if_exception_type((QwenOcrRetryableError, httpx.HTTPError)),
        wait=wait_exponential(multiplier=1, min=1, max=16),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    async def document_parsing(self, image_path: Path) -> dict[str, Any]:
        return await self._call_task(image_path=image_path, task="document_parsing")

    @retry(
        retry=retry_if_exception_type((QwenOcrRetryableError, httpx.HTTPError)),
        wait=wait_exponential(multiplier=1, min=1, max=16),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    async def formula_recognition(self, image_path: Path) -> dict[str, Any]:
        return await self._call_task(image_path=image_path, task="formula_recognition")

    async def _call_task(self, *, image_path: Path, task: str) -> dict[str, Any]:
        url = (
            f"{self._settings.dashscope_aigc_base_url.rstrip('/')}/"
            "services/aigc/multimodal-generation/generation"
        )
        headers = {
            "Authorization": f"Bearer {self._settings.dashscope_api_key}",
            "Content-Type": "application/json",
        }

        image_data_url = self._image_to_data_url(image_path)
        candidates = self._model_candidates()

        last_error: str | None = None
        for model in candidates:
            payload = self._build_payload(model=model, image_data_url=image_data_url, task=task)
            response = await self._client.post(url, headers=headers, json=payload)

            if response.status_code >= 500:
                raise QwenOcrRetryableError(f"Server error {response.status_code}: {response.text[:300]}")
            if response.status_code in {408, 429}:
                raise QwenOcrRetryableError(f"Transient status {response.status_code}: {response.text[:300]}")
            if response.status_code >= 400:
                code, message = self._extract_error(response)
                if self._is_model_not_exist(code=code, message=message):
                    last_error = f"{code}: {message}"
                    logger.warning("OCR model '%s' unavailable, trying fallback", model)
                    continue

                raise QwenOcrFatalError(
                    "OCR request failed "
                    f"task={task} model={model} status={response.status_code}: {response.text[:800]}"
                )

            data = response.json()
            self._resolved_model = model
            logger.debug("OCR raw %s", json.dumps(data, ensure_ascii=False)[:800])
            return data

        raise QwenOcrFatalError(
            f"No available OCR model from candidates={candidates}. Last error: {last_error}"
        )

    @staticmethod
    def _build_payload(*, model: str, image_data_url: str, task: str) -> dict[str, Any]:
        return {
            "model": model,
            "input": {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"text": "Extract all text and formulas exactly, preserve latex where possible."},
                            {"image": image_data_url},
                        ],
                    }
                ]
            },
            "parameters": {
                "result_format": "message",
                "min_pixels": 3136,
                "max_pixels": 12845056,
                "enable_rotate": True,
                "ocr_options": {
                    "task": task,
                },
            },
        }

    def _model_candidates(self) -> list[str]:
        ordered = [
            self._resolved_model,
            self._settings.qwen_ocr_model,
            "qwen-vl-ocr",
            "qwen-vl-ocr-latest",
            "qwen-vl-ocr-2025-11-20",
        ]

        out: list[str] = []
        seen: set[str] = set()
        for value in ordered:
            if not value:
                continue
            key = value.strip()
            if not key or key in seen:
                continue
            out.append(key)
            seen.add(key)
        return out

    @staticmethod
    def _extract_error(response: httpx.Response) -> tuple[str, str]:
        try:
            data = response.json()
            code = str(data.get("code") or "")
            message = str(data.get("message") or "")
            if code or message:
                return code, message
        except Exception:
            pass
        return "", response.text[:400]

    @staticmethod
    def _is_model_not_exist(*, code: str, message: str) -> bool:
        msg = message.lower()
        return code == "InvalidParameter" and "model" in msg and "exist" in msg

    @staticmethod
    def _image_to_data_url(path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix in {".jpg", ".jpeg"}:
            mime = "image/jpeg"
        elif suffix == ".png":
            mime = "image/png"
        elif suffix == ".webp":
            mime = "image/webp"
        else:
            mime = "application/octet-stream"

        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:{mime};base64,{encoded}"

    @staticmethod
    def extract_text(ocr_response: dict[str, Any]) -> str:
        output = ocr_response.get("output") or {}

        text = output.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()

        choices = output.get("choices") or []
        if not choices:
            return ""

        message = choices[0].get("message") or {}
        content = message.get("content")
        collected = QwenOcrClient._collect_text(content)
        return "\n".join(collected).strip()

    @staticmethod
    def _collect_text(value: Any) -> list[str]:
        result: list[str] = []

        def walk(node: Any) -> None:
            if isinstance(node, str):
                stripped = node.strip()
                if stripped:
                    result.append(stripped)
                return

            if isinstance(node, list):
                for item in node:
                    walk(item)
                return

            if isinstance(node, dict):
                for key in ("text", "latex", "markdown"):
                    val = node.get(key)
                    if isinstance(val, str):
                        stripped = val.strip()
                        if stripped:
                            result.append(stripped)
                if "content" in node:
                    walk(node["content"])
                return

        walk(value)

        deduped: list[str] = []
        seen: set[str] = set()
        for item in result:
            if item in seen:
                continue
            deduped.append(item)
            seen.add(item)
        return deduped
