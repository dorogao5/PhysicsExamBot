from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.config.settings import Settings

logger = logging.getLogger(__name__)


class DashScopeError(RuntimeError):
    pass


class DashScopeChatClient:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=20.0))

    async def close(self) -> None:
        await self._client.aclose()

    @retry(
        retry=retry_if_exception_type((DashScopeError, httpx.HTTPError)),
        wait=wait_exponential(multiplier=1, min=1, max=16),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    async def chat_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 1200,
        enable_thinking: bool = False,
        use_cache_prefix: bool = True,
    ) -> str:
        payload = self._build_payload(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=model or self._settings.qwen_chat_model,
            temperature=temperature,
            max_tokens=max_tokens,
            enable_thinking=enable_thinking,
            use_cache_prefix=use_cache_prefix,
        )
        data = await self._post_compatible_chat(payload)
        self._log_usage(data)
        return self._extract_text(data)

    @retry(
        retry=retry_if_exception_type((DashScopeError, httpx.HTTPError)),
        wait=wait_exponential(multiplier=1, min=1, max=16),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    async def chat_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema_name: str,
        schema: dict[str, Any],
        model: str | None = None,
        temperature: float = 0.1,
        max_tokens: int = 1600,
        enable_thinking: bool = False,
        use_cache_prefix: bool = True,
    ) -> dict[str, Any]:
        model_name = model or self._settings.qwen_chat_model
        token_attempts = self._build_token_attempts(max_tokens)
        last_error: json.JSONDecodeError | None = None

        for idx, token_limit in enumerate(token_attempts):
            payload = self._build_payload(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                model=model_name,
                temperature=temperature,
                max_tokens=token_limit,
                enable_thinking=enable_thinking,
                use_cache_prefix=use_cache_prefix,
            )
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "schema": schema,
                    "strict": True,
                },
            }

            data = await self._post_compatible_chat(payload)
            self._log_usage(data)
            text = self._extract_text(data)

            try:
                return self._parse_json_response_text(text)
            except json.JSONDecodeError as exc:
                last_error = exc
                finish_reason = self._extract_finish_reason(data)
                hit_limit = self._likely_hit_token_limit(data, token_limit)
                truncated = self._looks_truncated_json(text)
                should_retry_bigger = idx < len(token_attempts) - 1 and (hit_limit or truncated or finish_reason == "length")

                logger.warning(
                    "JSON parse failed (attempt=%s/%s max_tokens=%s finish_reason=%s hit_limit=%s truncated=%s): %s",
                    idx + 1,
                    len(token_attempts),
                    token_limit,
                    finish_reason,
                    hit_limit,
                    truncated,
                    exc,
                )

                if should_retry_bigger:
                    continue
                raise

        if last_error:
            raise last_error
        raise DashScopeError("Unexpected chat_json flow: no result and no parse error")

    def _build_payload(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        model: str,
        temperature: float,
        max_tokens: int,
        enable_thinking: bool,
        use_cache_prefix: bool,
    ) -> dict[str, Any]:
        if use_cache_prefix:
            system_content: str | list[dict[str, Any]] = [
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        else:
            system_content = system_prompt

        return {
            "model": model,
            "messages": [
                {"role": "system", "content": system_content},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "enable_thinking": enable_thinking,
        }

    async def _post_compatible_chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._settings.dashscope_base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._settings.dashscope_api_key}",
            "Content-Type": "application/json",
        }
        response = await self._client.post(url, headers=headers, json=payload)
        if response.status_code >= 500:
            raise DashScopeError(f"Server error {response.status_code}: {response.text[:300]}")
        if response.status_code == 429:
            raise DashScopeError(f"Rate limited: {response.text[:300]}")
        if response.status_code >= 400:
            raise DashScopeError(f"Request failed {response.status_code}: {response.text[:500]}")
        return response.json()

    @staticmethod
    def _extract_text(data: dict[str, Any]) -> str:
        choices = data.get("choices") or []
        if not choices:
            raise DashScopeError(f"No choices in completion: {data}")

        message = choices[0].get("message") or {}
        content = message.get("content")

        if isinstance(content, str):
            return content.strip()

        if isinstance(content, list):
            text_parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text = item.get("text")
                    if isinstance(text, str):
                        text_parts.append(text)
            if text_parts:
                return "\n".join(text_parts).strip()

        raise DashScopeError(f"Unexpected completion content: {content}")

    @staticmethod
    def _parse_json_response_text(text: str) -> dict[str, Any]:
        candidates = DashScopeChatClient._json_candidates(text)
        last_error: json.JSONDecodeError | None = None
        for candidate in candidates:
            try:
                value = json.loads(candidate)
            except json.JSONDecodeError as exc:
                last_error = exc
                continue
            if isinstance(value, dict):
                return value
            raise json.JSONDecodeError("Top-level JSON is not an object", candidate, 0)

        if last_error:
            raise last_error
        raise json.JSONDecodeError("No JSON object found", text, 0)

    @staticmethod
    def _json_candidates(text: str) -> list[str]:
        stripped = text.strip()
        candidates: list[str] = []
        if stripped:
            candidates.append(stripped)

        if "```" in stripped:
            parts = stripped.split("```")
            for part in parts:
                value = part.strip()
                if not value:
                    continue
                if value.startswith("json"):
                    value = value[4:].strip()
                if value.startswith("{") and value.endswith("}"):
                    candidates.append(value)

        start = stripped.find("{")
        end = stripped.rfind("}")
        if start != -1 and end != -1 and end > start:
            candidates.append(stripped[start : end + 1])

        unique: list[str] = []
        seen: set[str] = set()
        for value in candidates:
            if value in seen:
                continue
            unique.append(value)
            seen.add(value)
        return unique

    @staticmethod
    def _looks_truncated_json(text: str) -> bool:
        stripped = text.strip()
        if not stripped:
            return False
        if stripped.startswith("{") and not stripped.endswith("}"):
            return True
        if stripped.count("{") > stripped.count("}"):
            return True
        if stripped.endswith("\\") or stripped.endswith(',"') or stripped.endswith('"'):
            return True
        return False

    @staticmethod
    def _extract_finish_reason(data: dict[str, Any]) -> str | None:
        choices = data.get("choices") or []
        if not choices:
            return None
        reason = choices[0].get("finish_reason")
        return str(reason) if reason is not None else None

    @staticmethod
    def _likely_hit_token_limit(data: dict[str, Any], max_tokens: int) -> bool:
        usage = data.get("usage") or {}
        completion_tokens = usage.get("completion_tokens")
        if isinstance(completion_tokens, int):
            return completion_tokens >= max_tokens - 1
        return False

    @staticmethod
    def _build_token_attempts(max_tokens: int) -> list[int]:
        ceiling = 4096
        values = [
            max_tokens,
            min(ceiling, int(max_tokens * 1.5)),
            min(ceiling, max_tokens * 2),
        ]
        out: list[int] = []
        for value in values:
            if value <= 0:
                continue
            if value not in out:
                out.append(value)
        return out or [max_tokens]

    @staticmethod
    def _log_usage(data: dict[str, Any]) -> None:
        usage = data.get("usage") or {}
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")

        cached_tokens: int | None = None
        prompt_details = usage.get("prompt_tokens_details")
        if isinstance(prompt_details, dict):
            cached_tokens = prompt_details.get("cached_tokens")

        logger.info(
            "LLM usage prompt=%s completion=%s cached=%s",
            prompt_tokens,
            completion_tokens,
            cached_tokens,
        )
