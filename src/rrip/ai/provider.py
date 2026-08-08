"""LLM provider abstraction: Gemini free tier primary, Groq alternate.

Free-tier quotas are the binding constraint, so rate limiting, 429 backoff and a
development response cache are part of the interface rather than bolted on. A
provider is selected by config, never hardcoded.

THE ARCHITECTURAL RULE THIS SERVES: the model never computes a number. It
proposes SQL, explains results, and suggests confounders. Every figure that
reaches a user is computed by Postgres, Python or statsmodels. Nothing in this
module returns a statistic.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from rrip.config import PROJECT_ROOT

CACHE_DIR = PROJECT_ROOT / ".cache" / "llm"


@dataclass
class LLMResponse:
    text: str
    provider: str
    model: str
    cached: bool = False
    duration_ms: float = 0.0
    attempts: int = 1
    prompt_tokens: int | None = None


@dataclass
class RateLimiter:
    """Token-bucket limiter sized for free-tier quotas.

    Gemini's free tier is roughly 15 requests/minute; Groq's varies by model.
    Defaults are deliberately conservative -- being throttled locally is cheaper
    than being throttled remotely, where the penalty is a 429 and a backoff.
    """

    requests_per_minute: int = 12
    _timestamps: list[float] = field(default_factory=list)

    async def acquire(self) -> float:
        now = time.monotonic()
        self._timestamps = [t for t in self._timestamps if now - t < 60.0]
        waited = 0.0
        if len(self._timestamps) >= self.requests_per_minute:
            sleep_for = 60.0 - (now - self._timestamps[0]) + 0.05
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)
                waited = sleep_for
            now = time.monotonic()
            self._timestamps = [t for t in self._timestamps if now - t < 60.0]
        self._timestamps.append(time.monotonic())
        return waited


class LLMProvider(ABC):
    name: str = "abstract"
    model: str = ""

    def __init__(self, api_key: str | None = None, rpm: int = 12,
                 use_cache: bool = True) -> None:
        self.api_key = api_key
        self.limiter = RateLimiter(requests_per_minute=rpm)
        self.use_cache = use_cache

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def _cache_path(self, prompt: str, system: str) -> Path:
        key = hashlib.sha256(
            f"{self.name}|{self.model}|{system}|{prompt}".encode()).hexdigest()[:24]
        return CACHE_DIR / f"{self.name}-{key}.json"

    def _read_cache(self, prompt: str, system: str) -> str | None:
        if not self.use_cache:
            return None
        p = self._cache_path(prompt, system)
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))["text"]
            except Exception:
                return None
        return None

    def _write_cache(self, prompt: str, system: str, text: str) -> None:
        if not self.use_cache:
            return
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self._cache_path(prompt, system).write_text(
            json.dumps({"text": text, "prompt": prompt[:500]}), encoding="utf-8")

    @abstractmethod
    async def _call(self, prompt: str, system: str, temperature: float) -> str: ...

    async def complete(self, prompt: str, system: str = "",
                       temperature: float = 0.0, max_retries: int = 4) -> LLMResponse:
        cached = self._read_cache(prompt, system)
        if cached is not None:
            return LLMResponse(cached, self.name, self.model, cached=True)

        if not self.available:
            raise LLMUnavailable(
                f"{self.name} has no API key configured. Set the provider's key "
                "in .env, or use FakeProvider for offline development.")

        last_exc: Exception | None = None
        for attempt in range(1, max_retries + 1):
            await self.limiter.acquire()
            t0 = time.perf_counter()
            try:
                text = await self._call(prompt, system, temperature)
                self._write_cache(prompt, system, text)
                return LLMResponse(text, self.name, self.model,
                                   duration_ms=(time.perf_counter() - t0) * 1000,
                                   attempts=attempt)
            except RateLimited as exc:
                last_exc = exc
                # Exponential backoff with jitter. Jitter matters: without it,
                # concurrent callers retry in lockstep and re-trigger the limit.
                delay = min(2 ** attempt + random.uniform(0, 1), 60)
                if exc.retry_after:
                    delay = max(delay, exc.retry_after)
                await asyncio.sleep(delay)
            except Exception as exc:
                last_exc = exc
                if attempt == max_retries:
                    break
                await asyncio.sleep(min(2 ** attempt, 20))
        raise LLMError(f"{self.name} failed after {max_retries} attempts: {last_exc}")


class LLMError(RuntimeError):
    pass


class LLMUnavailable(LLMError):
    pass


class RateLimited(LLMError):
    def __init__(self, msg: str, retry_after: float | None = None) -> None:
        super().__init__(msg)
        self.retry_after = retry_after


class GeminiProvider(LLMProvider):
    name = "gemini"
    model = "gemini-2.0-flash"
    endpoint = "https://generativelanguage.googleapis.com/v1beta/models"

    async def _call(self, prompt: str, system: str, temperature: float) -> str:
        body: dict = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": temperature,
                                 "maxOutputTokens": 2048},
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        url = f"{self.endpoint}/{self.model}:generateContent"
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(url, params={"key": self.api_key}, json=body)
        if r.status_code == 429:
            raise RateLimited("gemini 429", _retry_after(r))
        r.raise_for_status()
        data = r.json()
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as exc:
            raise LLMError(f"unexpected gemini response shape: {str(data)[:300]}") from exc


class GroqProvider(LLMProvider):
    name = "groq"
    model = "llama-3.3-70b-versatile"
    endpoint = "https://api.groq.com/openai/v1/chat/completions"

    async def _call(self, prompt: str, system: str, temperature: float) -> str:
        messages = ([{"role": "system", "content": system}] if system else []) + [
            {"role": "user", "content": prompt}]
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                self.endpoint,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"model": self.model, "messages": messages,
                      "temperature": temperature, "max_tokens": 2048})
        if r.status_code == 429:
            raise RateLimited("groq 429", _retry_after(r))
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


class FakeProvider(LLMProvider):
    """Deterministic provider for tests and offline development.

    Responses are supplied by the caller, so the validation layers can be tested
    against adversarial output -- including output that invents numbers, which is
    exactly what the grounding guard exists to catch.
    """

    name = "fake"
    model = "fake-1"

    def __init__(self, responses: list[str] | None = None) -> None:
        super().__init__(api_key="fake", use_cache=False)
        self.responses = list(responses or [])
        self.calls: list[dict] = []

    @property
    def available(self) -> bool:
        return True

    async def _call(self, prompt: str, system: str, temperature: float) -> str:
        self.calls.append({"prompt": prompt, "system": system})
        if not self.responses:
            raise LLMError("FakeProvider ran out of scripted responses")
        return self.responses.pop(0)


def _retry_after(r: httpx.Response) -> float | None:
    v = r.headers.get("retry-after")
    try:
        return float(v) if v else None
    except ValueError:
        return None


def get_provider(name: str | None = None, use_cache: bool = True) -> LLMProvider:
    """Select a provider from config. Never hardcoded at a call site."""
    chosen = (name or os.getenv("RRIP_LLM_PROVIDER") or "gemini").lower()
    if chosen == "gemini":
        return GeminiProvider(os.getenv("GEMINI_API_KEY"), rpm=12, use_cache=use_cache)
    if chosen == "groq":
        return GroqProvider(os.getenv("GROQ_API_KEY"), rpm=25, use_cache=use_cache)
    if chosen == "fake":
        return FakeProvider()
    raise ValueError(f"unknown provider {chosen!r}; expected gemini, groq or fake")
