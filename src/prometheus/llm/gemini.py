"""Async Gemini wrapper. 10s timeout, 1 retry, fallback handling.

If GEMINI_API_KEY is unset, all calls return None — callers must have a hardcoded
fallback path.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from prometheus.config import settings
from prometheus.utils.logging import log

_TIMEOUT_S = 10.0
_RETRIES = 1


class GeminiClient:
    def __init__(self) -> None:
        self.enabled = bool(settings.gemini_api_key)
        self._client: Any = None
        if self.enabled:
            try:
                from google import genai

                self._client = genai.Client(api_key=settings.gemini_api_key)
            except Exception as exc:  # noqa: BLE001
                log.error("gemini.init_failed", error=str(exc))
                self.enabled = False

    async def generate_text(
        self,
        *,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.4,
    ) -> str | None:
        if not self.enabled or self._client is None:
            return None

        from google.genai import types

        cfg = types.GenerateContentConfig(
            system_instruction=system,
            temperature=temperature,
        )

        return await self._call(prompt=prompt, config=cfg, parse_json=False)

    async def generate_json(
        self,
        *,
        prompt: str,
        schema: dict | type,
        system: str | None = None,
        temperature: float = 0.2,
    ) -> dict | None:
        if not self.enabled or self._client is None:
            return None

        from google.genai import types

        cfg = types.GenerateContentConfig(
            system_instruction=system,
            temperature=temperature,
            response_mime_type="application/json",
            response_schema=schema,
        )

        return await self._call(prompt=prompt, config=cfg, parse_json=True)

    async def _call(self, *, prompt: str, config: Any, parse_json: bool):
        last_err: Exception | None = None
        for attempt in range(_RETRIES + 1):
            try:
                resp = await asyncio.wait_for(
                    self._client.aio.models.generate_content(
                        model=settings.gemini_model,
                        contents=prompt,
                        config=config,
                    ),
                    timeout=_TIMEOUT_S,
                )
                text = (resp.text or "").strip()
                if parse_json:
                    if not text:
                        return None
                    try:
                        return json.loads(text)
                    except json.JSONDecodeError as je:
                        log.warning(
                            "gemini.json_decode_failed",
                            attempt=attempt,
                            text_head=text[:200],
                            error=str(je),
                        )
                        last_err = je
                        continue
                return text
            except TimeoutError as te:
                log.warning("gemini.timeout", attempt=attempt)
                last_err = te
            except Exception as exc:  # noqa: BLE001
                log.warning("gemini.call_failed", attempt=attempt, error=str(exc))
                last_err = exc
        log.error("gemini.gave_up", error=str(last_err) if last_err else None)
        return None


_singleton: GeminiClient | None = None


def gemini() -> GeminiClient:
    global _singleton
    if _singleton is None:
        _singleton = GeminiClient()
    return _singleton
