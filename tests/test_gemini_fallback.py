"""Gemini model fallback: an overloaded model hands over to the next one."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from rrip.ai import provider as P


def _patch_transport(monkeypatch, handler):
    real = httpx.AsyncClient

    def client(*a, **kw):
        kw["transport"] = httpx.MockTransport(handler)
        return real(*a, **kw)

    monkeypatch.setattr(P.httpx, "AsyncClient", client)


def _ok(text: str) -> httpx.Response:
    return httpx.Response(200, json={"candidates": [
        {"finishReason": "STOP", "content": {"parts": [{"text": text}]}}]})


def test_503_on_first_model_falls_through_to_the_next(monkeypatch) -> None:
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        model = req.url.path.rsplit("/", 1)[-1].split(":")[0]
        seen.append(model)
        if model == P.GeminiProvider.MODELS[0]:
            return httpx.Response(503, json={"error": {"status": "UNAVAILABLE"}})
        return _ok("SELECT 1")

    _patch_transport(monkeypatch, handler)
    g = P.GeminiProvider(api_key="test-key", use_cache=False)
    assert asyncio.run(g._call("q", "", 0.0)) == "SELECT 1"
    assert seen[:2] == list(P.GeminiProvider.MODELS[:2])
    assert g.model == P.GeminiProvider.MODELS[1]


def test_503_on_every_model_is_rate_limited_not_a_hard_error(monkeypatch) -> None:
    _patch_transport(monkeypatch, lambda req: httpx.Response(503, text="busy"))
    g = P.GeminiProvider(api_key="test-key", use_cache=False)
    with pytest.raises(P.RateLimited):
        asyncio.run(g._call("q", "", 0.0))


def test_the_key_never_appears_in_an_error(monkeypatch) -> None:
    _patch_transport(monkeypatch, lambda req: httpx.Response(500, text="boom"))
    g = P.GeminiProvider(api_key="sekrit-key-123", use_cache=False)
    with pytest.raises(P.LLMError) as exc:
        asyncio.run(g._call("q", "", 0.0))
    assert "sekrit-key-123" not in str(exc.value)
