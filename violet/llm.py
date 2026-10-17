"""A tiny, resilient client for any OpenAI-compatible /chat/completions API.

Built for free hosting: no SDK, one shared aiohttp session, bounded concurrency, retries with backoff,
a circuit breaker that stops hammering a failing provider, and every failure turned into a typed error.
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any

import aiohttp

from .config import Settings
from .utils import strip_reasoning, truncate

log = logging.getLogger("violet.llm")

MAX_ATTEMPTS = 3
BREAKER_THRESHOLD = 5  # consecutive failed requests
BREAKER_COOLDOWN = 60.0  # seconds
MAX_RETRY_AFTER = 8.0


class LLMError(Exception):
    """Base class for all language-service failures."""


class LLMNotConfigured(LLMError):
    pass


class LLMAuthError(LLMError):
    pass


class LLMUnavailable(LLMError):
    pass


class LLMRateLimited(LLMError):
    pass


class LLMBadResponse(LLMError):
    pass


class LLMClient:
    def __init__(
        self,
        settings: Settings,
        session: aiohttp.ClientSession,
        *,
        sleep=asyncio.sleep,
        clock=time.monotonic,
    ) -> None:
        self._s = settings
        self._session = session
        self._sleep = sleep
        self._clock = clock
        self._sem = asyncio.Semaphore(settings.llm_max_concurrent)
        self._consecutive_failures = 0
        self._open_until = 0.0
        self.calls_total = 0
        self.errors_total = 0
        self.last_error: str | None = None

    # ------------------------------------------------------------------ status
    @property
    def configured(self) -> bool:
        return self._s.llm_configured

    @property
    def breaker_open(self) -> bool:
        return self._clock() < self._open_until

    # ------------------------------------------------------------------ public
    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> str:
        if not self.configured:
            raise LLMNotConfigured("LLM_API_KEY / LLM_BASE_URL / LLM_MODEL are not all set")
        if self.breaker_open:
            raise LLMUnavailable("language service is cooling down after repeated failures")

        payload = {
            "model": self._s.llm_model,
            "messages": messages,
            "max_tokens": max_tokens or self._s.llm_max_tokens,
            "temperature": self._s.llm_temperature if temperature is None else temperature,
            "stream": False,
        }
        async with self._sem:
            self.calls_total += 1
            try:
                text = await self._request_with_retry(payload)
            except LLMAuthError:
                self._note_error("auth error (check LLM_API_KEY)")
                raise  # configuration problem, not an outage: do not trip the breaker
            except LLMBadResponse as exc:
                self._note_error(str(exc))
                raise
            except LLMError as exc:
                self._note_error(str(exc))
                self._record_failure()
                raise
        self._consecutive_failures = 0
        return text

    # ------------------------------------------------------------------ internals
    def _note_error(self, message: str) -> None:
        self.errors_total += 1
        self.last_error = truncate(message, 200)
        log.warning("LLM request failed: %s", self.last_error)

    def _record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= BREAKER_THRESHOLD:
            self._open_until = self._clock() + BREAKER_COOLDOWN
            self._consecutive_failures = 0
            log.error("LLM circuit breaker opened for %.0fs", BREAKER_COOLDOWN)

    async def _request_with_retry(self, payload: dict[str, Any]) -> str:
        headers = {"Authorization": f"Bearer {self._s.llm_api_key}", "Content-Type": "application/json"}
        timeout = aiohttp.ClientTimeout(total=self._s.llm_timeout)
        last_problem = "unknown error"

        for attempt in range(1, MAX_ATTEMPTS + 1):
            delay = 1.0 * (2 ** (attempt - 1)) + random.uniform(0, 0.3)
            try:
                async with self._session.post(self._s.chat_url, json=payload, headers=headers, timeout=timeout) as resp:
                    if resp.status == 200:
                        return self._parse(await resp.json(content_type=None))

                    body = truncate((await resp.text()).replace("\n", " "), 200)
                    if resp.status in (401, 403):
                        raise LLMAuthError(f"HTTP {resp.status}: {body}")
                    if resp.status == 429:
                        last_problem = "rate limited by the provider"
                        retry_after = _parse_retry_after(resp.headers.get("Retry-After"))
                        delay = min(retry_after if retry_after is not None else delay, MAX_RETRY_AFTER)
                        if attempt == MAX_ATTEMPTS:
                            raise LLMRateLimited(last_problem)
                    elif resp.status >= 500 or resp.status in (408, 409):
                        last_problem = f"provider error HTTP {resp.status}"
                    else:
                        raise LLMBadResponse(f"HTTP {resp.status}: {body}")
            except (asyncio.TimeoutError, aiohttp.ClientError) as exc:
                last_problem = f"network problem: {type(exc).__name__}"
            except (LLMAuthError, LLMBadResponse, LLMRateLimited):
                raise
            except (ValueError, aiohttp.ContentTypeError) as exc:  # invalid JSON body
                raise LLMBadResponse(f"unreadable response: {exc}") from exc

            if attempt < MAX_ATTEMPTS:
                log.info("LLM attempt %d/%d failed (%s); retrying in %.1fs", attempt, MAX_ATTEMPTS, last_problem, delay)
                await self._sleep(delay)

        raise LLMUnavailable(last_problem)

    @staticmethod
    def _parse(data: Any) -> str:
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMBadResponse("response had no choices[0].message.content") from exc

        if isinstance(content, list):  # some providers return a list of parts
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        if not isinstance(content, str):
            raise LLMBadResponse("message content was not text")

        text = strip_reasoning(content)
        if not text:
            raise LLMBadResponse("the model returned an empty reply")
        return text


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None
