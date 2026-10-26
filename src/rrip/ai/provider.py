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
import random
import tempfile
import time
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from rrip.config import PROJECT_ROOT


def _cache_dir() -> Path:
    """Where to keep the development response cache.

    Serverless filesystems are read-only apart from a temp directory, so the
    repo-relative default is unwritable in production -- writing there raised
    OSError, which propagated as an LLM failure and was retried four times
    against a filesystem that was never going to become writable.

    Falls back to the system temp directory when the repo is not writable.
    """
    preferred = PROJECT_ROOT / ".cache" / "llm"
    try:
        preferred.mkdir(parents=True, exist_ok=True)
        probe = preferred / ".writable"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        return preferred
    except OSError:
        return Path(tempfile.gettempdir()) / "rrip-llm-cache"


CACHE_DIR = _cache_dir()


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
                 use_cache: bool = True, max_retries: int = 4) -> None:
        self.api_key = api_key
        self.limiter = RateLimiter(requests_per_minute=rpm)
        self.use_cache = use_cache
        # How many provider attempts complete() makes. An attribute so a caller
        # with a time budget can lower it: the API sets 2 (guarded_provider),
        # because a request runs inside a 60 s serverless function and four
        # attempts with backoff do not fit. The CLI and benchmarks keep 4.
        self.max_retries = max_retries
        # Awaited before every paid attempt (not for cache hits). The API sets
        # it to the shared daily-cap counter in rrip.api.limits; it raises a
        # CallRefused to stop the call. None everywhere else -- the CLI and the
        # benchmarks are not rationed.
        self.budget: Callable[[], Awaitable[None]] | None = None

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def _cache_path(self, prompt: str, system: str, model: str) -> Path:
        # Keyed on the model PASSED, not self.model: GeminiProvider rewrites
        # self.model to whichever fallback answered, so a key read from
        # self.model would be the preferred model on the read and the fallback
        # on the write -- a cache that never hit after a single fallback.
        key = hashlib.sha256(
            f"{self.name}|{model}|{system}|{prompt}".encode()).hexdigest()[:24]
        return CACHE_DIR / f"{self.name}-{key}.json"

    def _read_cache(self, prompt: str, system: str, model: str) -> str | None:
        if not self.use_cache:
            return None
        try:
            p = self._cache_path(prompt, system, model)
            if p.exists():
                return json.loads(p.read_text(encoding="utf-8"))["text"]
        except (OSError, ValueError, KeyError):
            return None
        return None

    def _write_cache(self, prompt: str, system: str, model: str, text: str) -> None:
        """Best effort. A cache is an optimisation, never a failure path.

        This previously let an OSError escape, so a read-only filesystem turned
        a successful model response into a failed request.
        """
        if not self.use_cache:
            return
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            self._cache_path(prompt, system, model).write_text(
                json.dumps({"text": text, "prompt": prompt[:500]}), encoding="utf-8")
        except OSError:
            pass

    @abstractmethod
    async def _call(self, prompt: str, system: str, temperature: float) -> str: ...

    async def complete(self, prompt: str, system: str = "",
                       temperature: float = 0.0,
                       max_retries: int | None = None) -> LLMResponse:
        model = self.model          # stable for the cache; _call may rewrite self.model
        cached = self._read_cache(prompt, system, model)
        if cached is not None:
            return LLMResponse(cached, self.name, model, cached=True)

        if not self.available:
            raise LLMUnavailable(
                f"{self.name} has no API key configured. Set the provider's key "
                "in .env, or use FakeProvider for offline development.")

        retries = self.max_retries if max_retries is None else max_retries
        last_exc: Exception | None = None
        for attempt in range(1, retries + 1):
            await self.limiter.acquire()
            # Outside the try below on purpose: that block retries on any
            # exception, and a refused budget must end the call, not be retried.
            if self.budget is not None:
                await self.budget()
            t0 = time.perf_counter()
            try:
                text = await self._call(prompt, system, temperature)
                self._write_cache(prompt, system, model, text)
                return LLMResponse(text, self.name, self.model,
                                   duration_ms=(time.perf_counter() - t0) * 1000,
                                   attempts=attempt)
            except (CallRefused, NonRetryable):
                # A budget refusal, a 4xx, or a truncated response: retrying
                # cannot change the outcome and only burns the time budget and
                # (for the cap) another charged attempt. End the call now.
                raise
            except RateLimited as exc:
                last_exc = exc
                if attempt == retries:
                    break                 # no backoff sleep after the last try
                # Exponential backoff with jitter. Jitter matters: without it,
                # concurrent callers retry in lockstep and re-trigger the limit.
                delay = min(2 ** attempt + random.uniform(0, 1), 20)
                if exc.retry_after:
                    delay = max(delay, min(exc.retry_after, 20))
                await asyncio.sleep(delay)
            except Exception as exc:
                last_exc = exc
                if attempt == retries:
                    break
                await asyncio.sleep(min(2 ** attempt, 20))
        raise LLMError(f"{self.name} failed after {retries} attempts: "
                       f"{type(last_exc).__name__}: {last_exc}")


class LLMError(RuntimeError):
    pass


class LLMUnavailable(LLMError):
    pass


class CallRefused(LLMError):
    """The request was refused by abuse protection before any model call.

    Every LLM call site catches broad exceptions to report provider failures
    as data; each re-raises this, so a refusal reaches the API as a refusal
    instead of being recorded as a "provider error" or degraded around.
    Carries only what is safe to show a client.
    """

    status = 503
    code = "REFUSED"

    def __init__(self, message: str, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.public_message = message
        self.retry_after = retry_after


class RateLimited(LLMError):
    def __init__(self, msg: str, retry_after: float | None = None) -> None:
        super().__init__(msg)
        self.retry_after = retry_after


class NonRetryable(LLMError):
    """A failure that a retry cannot fix: a 4xx other than 429 (a bad request,
    an auth or quota error), or a response the model truncated. complete() ends
    the call on this rather than spending its remaining attempts and, under the
    API cap, charging for each one."""


class GeminiProvider(LLMProvider):
    """Gemini free tier.

    MODEL FALLBACK, and why it is not over-engineering: free-tier model
    availability shifts without notice. Measured against a fresh key:

        gemini-2.0-flash    429  "You exceeded your current quota"
        gemini-2.5-flash    404  "no longer available to new users"
        gemini-flash-latest 200  works

    A 429 on a brand-new key is not throttling to back off from -- it means
    that model has no free quota at all, and retrying it forever is wasted
    time. So a 429 or 404 advances to the next model rather than sleeping,
    and only the last model in the chain raises RateLimited.
    """

    name = "gemini"
    # Ordered by preference. `-latest` aliases survive model retirements, which
    # is exactly what broke the first two entries.
    MODELS = ("gemini-flash-latest", "gemini-2.0-flash", "gemini-flash-lite-latest")
    model = MODELS[0]
    endpoint = "https://generativelanguage.googleapis.com/v1beta/models"

    async def _call(self, prompt: str, system: str, temperature: float) -> str:
        body: dict = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": temperature,
                # 8192, not 2048. The `-latest` aliases point at thinking
                # models whose internal reasoning is billed against this same
                # budget, so a 2048 ceiling left roughly 140 characters for the
                # actual answer -- responses were truncated mid-JSON and
                # surfaced as "response was not valid JSON", which points at
                # the wrong problem entirely.
                #
                # thinkingConfig/thinkingBudget is NOT sent: this endpoint
                # rejects it with 400 for these models. Headroom is the fix.
                "maxOutputTokens": 8192,
            },
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}

        # The key goes in a header, NOT ?key=. httpx includes the full request
        # URL in HTTPStatusError messages, so a query-parameter key is printed
        # verbatim into logs and tracebacks on every failure.
        headers = {"x-goog-api-key": self.api_key or ""}

        last = ""
        async with httpx.AsyncClient(timeout=60) as client:
            for i, model in enumerate(self.MODELS):
                url = f"{self.endpoint}/{model}:generateContent"
                r = await client.post(url, headers=headers, json=body)

                # 503 joins 404/429: "model is currently experiencing high
                # demand" is about that model, not the request, and retrying
                # the same overloaded model four times while a working one sits
                # next in the chain turned a demand spike into a failed answer.
                if r.status_code in (404, 429, 503):
                    last = _upstream_error(r)
                    if i < len(self.MODELS) - 1:
                        continue  # this model is unavailable; try the next
                    raise RateLimited(
                        f"gemini: every model in the fallback chain was "
                        f"unavailable (last: {last})", _retry_after(r))

                if r.status_code >= 400:
                    # Raise our own error rather than httpx's, which embeds the
                    # request URL and any credentials it carries. A 4xx here is
                    # the request itself (bad key, bad body) -- not transient --
                    # so it is non-retryable and ends the call at once.
                    raise NonRetryable(f"gemini {model} returned {_upstream_error(r)}")
                data = r.json()
                # Record which model actually answered, so logs and responses
                # report the truth rather than the preference.
                self.model = model

                cand = (data.get("candidates") or [{}])[0]
                finish = cand.get("finishReason")
                if finish and finish not in ("STOP", "FINISH_REASON_UNSPECIFIED"):
                    # Report truncation as truncation. A MAX_TOKENS cut lands
                    # mid-JSON and would otherwise be reported by the caller as
                    # a parse error, sending the reader after the wrong bug.
                    # Non-retryable: the same prompt truncates the same way.
                    raise NonRetryable(
                        f"gemini stopped early (finishReason={finish}); the "
                        "response is incomplete. Raise maxOutputTokens or "
                        "shorten the input.")
                try:
                    return cand["content"]["parts"][0]["text"]
                except (KeyError, IndexError) as exc:
                    # What was missing, not what arrived: `data` is the model's
                    # output, i.e. possibly the generated SQL itself.
                    raise LLMError("unexpected gemini response shape: no "
                                   "candidates[0].content.parts[0].text") from exc

        raise LLMError(f"gemini exhausted all models (last: {last})")


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
        if 400 <= r.status_code < 500:
            raise NonRetryable(f"groq returned {_upstream_error(r)}")
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


# Error codes repeated into messages, BY ALLOWLIST. A syntactic check ("one
# word-like token") is not enough: an API key or a hyphenated prompt fragment
# is also one token, and the field is whatever the provider sent. Anything not
# listed collapses to the bare HTTP status; the message already names the
# provider and model.
_KNOWN_ERROR_CODES = frozenset({
    # Gemini: error.status is always a google.rpc.Code name.
    "CANCELLED", "UNKNOWN", "INVALID_ARGUMENT", "DEADLINE_EXCEEDED", "NOT_FOUND",
    "ALREADY_EXISTS", "PERMISSION_DENIED", "UNAUTHENTICATED", "RESOURCE_EXHAUSTED",
    "FAILED_PRECONDITION", "ABORTED", "OUT_OF_RANGE", "UNIMPLEMENTED", "INTERNAL",
    "UNAVAILABLE", "DATA_LOSS",
    # Groq (OpenAI-compatible): error.type, and the common error.code values.
    "invalid_request_error", "authentication_error", "permission_error",
    "not_found_error", "rate_limit_error", "api_error", "internal_server_error",
    "invalid_api_key", "model_not_found", "model_decommissioned",
    "rate_limit_exceeded", "context_length_exceeded", "json_validate_failed",
})


def _upstream_error(r: httpx.Response) -> str:
    """'400 INVALID_ARGUMENT' -- the status and a RECOGNISED error code only.

    Never the response body. These messages become log lines, Sentry
    breadcrumbs and chained exceptions, and an error body is provider-controlled
    text that can echo the request -- the prompt, the schema, generated SQL.
    Status plus a known code tells a bad key from a quota from an outage; an
    unknown code is dropped rather than trusted.
    """
    try:
        err = r.json().get("error")
    except (ValueError, AttributeError):
        err = None
    if isinstance(err, dict):
        for key in ("status", "code", "type"):
            v = err.get(key)
            if isinstance(v, str) and v in _KNOWN_ERROR_CODES:
                return f"{r.status_code} {v}"
    return str(r.status_code)


def get_provider(name: str | None = None, use_cache: bool = True) -> LLMProvider:
    """Select a provider from config. Never hardcoded at a call site.

    Keys come from the Settings object, NOT os.getenv(). pydantic-settings loads
    .env into Settings and does not export to the process environment, so
    os.getenv() silently returns None for a key that is correctly configured --
    which presents as "provider not available" with no indication why.
    """
    from rrip.config import settings

    chosen = (name or settings.llm_provider or "gemini").lower()
    if chosen == "gemini":
        return GeminiProvider(settings.gemini_api_key or None, rpm=12,
                              use_cache=use_cache)
    if chosen == "groq":
        return GroqProvider(settings.groq_api_key or None, rpm=25,
                            use_cache=use_cache)
    if chosen == "fake":
        return FakeProvider()
    raise ValueError(f"unknown provider {chosen!r}; expected gemini, groq or fake")
