"""The Sentry privacy boundary, asserted on the real sanitiser functions.

These do not initialise Sentry or send anything. They build synthetic events
carrying unmistakable SENTINEL secrets in every place a secret could ride --
headers (mixed case), cookies, the AI request body, generated SQL, breadcrumbs,
span data, URL query strings, nested extras -- run the actual before_send /
before_send_transaction / before_breadcrumb callbacks, and assert the sentinel
values are nowhere in the final event. Checking that a callback ran is not
enough; the check is that the secret itself is gone.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys

import httpx
import pytest
from fastapi.testclient import TestClient

from rrip.ai import provider as P
from rrip.api import ai_routes, limits
from rrip.api import observability as obs
from rrip.config import PROJECT_ROOT, settings

# Distinct sentinels so a leak names exactly which path failed.
S = {
    "auth": "SENTINEL-AUTH-7a1",
    "goog": "AIzaSENTINELKEYxxxxxxxxxxxxxxxxxxxxxxxx",
    "xkey": "SENTINEL-XAPIKEY-7a2",
    "cookie": "SENTINEL-COOKIE-7a3",
    "session": "SENTINEL-SESSION-7a4",
    "prompt": "SENTINEL-PROMPT-7a5",
    "sql": "SENTINEL-SQL-7a6",
    "extra_sql": "SENTINEL-EXTRASQL-7a7",
    "dsn_pw": "SENTINEL-DSNPW-7a8",
    "env": "SENTINEL-ENV-7a9",
    "qs": "SENTINEL-QUERYSTRING-7b0",
    "crumb_sql": "SENTINEL-CRUMBSQL-7b1",
    "crumb_data": "SENTINEL-CRUMBDATA-7b2",
    "span_sql": "SENTINEL-SPANSQL-7b3",
    "tx_meta": "SENTINEL-TXMETA-7b4",
    "groq": "gsk_SENTINELgroqkeyxxxxxxxxxxxxxxxxxx",
}


def _event() -> dict:
    return {
        "level": "error",
        "exception": {"values": [{"type": "ValueError", "value": "a safe message"}]},
        "request": {
            "url": f"https://site/api/v1/ai/query?provider=fake&token={S['qs']}",
            "method": "POST",
            "query_string": f"provider=fake&token={S['qs']}",
            "headers": {
                "Authorization": f"Bearer {S['auth']}",
                "X-Goog-Api-Key": S["goog"],       # mixed case
                "x-api-key": S["xkey"],             # lowercase
                "Cookie": f"s={S['cookie']}",
                "User-Agent": "safe-agent/1.0",
                "Content-Type": "application/json",
            },
            "cookies": {"session": S["session"]},
            "data": {"question": S["prompt"], "sql": f"SELECT {S['sql']}"},
            "env": {"SERVER_NAME": S["env"]},
        },
        "extra": {
            "sql": f"SELECT {S['extra_sql']}",             # sensitive key
            "dsn": f"postgresql://u:{S['dsn_pw']}@h:5432/db",  # sensitive key + secret value
            "groq_key": S["groq"],                         # caught by the value pattern (gsk_)
            "token": S["tx_meta"],                         # sensitive key
            "note": "this-is-safe",                        # kept
        },
        "breadcrumbs": {"values": [
            {"category": "query", "message": f"SELECT {S['crumb_sql']}",
             "data": {"db.statement": f"SELECT {S['crumb_data']}"}, "level": "info"},
            {"category": "http", "message": "GET https://api/ok", "level": "info"},
        ]},
        "spans": [
            {"op": "db.query", "description": f"SELECT {S['span_sql']}", "data": {}},
            {"op": "http.client", "description": "GET /ok"},
        ],
        "contexts": {"trace": {"op": "http.server", "status": "internal_error"}},
        "tags": {"route": "/api/v1/ai/query", "transaction": "customers-page"},
    }


def _flat(event) -> str:
    return json.dumps(event, default=str)


def test_no_sentinel_secret_survives_before_send() -> None:
    scrubbed = obs.before_send(_event(), {})
    blob = _flat(scrubbed)
    leaked = [name for name, val in S.items() if val in blob]
    assert not leaked, f"secrets leaked through before_send: {leaked}"


def test_no_sentinel_secret_survives_before_send_transaction() -> None:
    # Transactions carry spans and operation metadata; same boundary applies.
    scrubbed = obs.before_send_transaction(_event(), {})
    blob = _flat(scrubbed)
    leaked = [name for name, val in S.items() if val in blob]
    assert not leaked, f"secrets leaked through before_send_transaction: {leaked}"


def test_before_breadcrumb_redacts_a_sql_breadcrumb_message() -> None:
    crumb = {"category": "query", "message": f"SELECT {S['crumb_sql']}",
             "data": {"db.statement": f"SELECT {S['crumb_data']}"}}
    out = obs.before_breadcrumb(crumb, {})
    blob = _flat(out)
    assert S["crumb_sql"] not in blob and S["crumb_data"] not in blob
    assert out["category"] == "query"        # safe diagnostic kept


def test_header_scrubbing_is_case_insensitive() -> None:
    for name in ("AUTHORIZATION", "authorization", "X-Goog-Api-Key", "x_goog_api_key"):
        ev = {"request": {"headers": {name: "SENTINEL-HEADER"}}}
        assert "SENTINEL-HEADER" not in _flat(obs.before_send(ev, {}))


def test_credential_in_url_or_value_is_redacted_even_under_a_safe_key() -> None:
    ev = {"extra": {"innocent": "postgresql://user:SENTINEL-PW@host/db",
                    "also": "AIzaSENTINEL1234567890abcdefghijklmnop"}}
    blob = _flat(obs.before_send(ev, {}))
    assert "SENTINEL-PW" not in blob and "AIzaSENTINEL" not in blob


def test_safe_diagnostics_are_retained() -> None:
    scrubbed = obs.before_send(_event(), {})
    blob = _flat(scrubbed)
    # Exception type, HTTP method, route and non-sensitive header survive.
    assert "ValueError" in blob
    assert "a safe message" in blob
    assert "/api/v1/ai/query" in blob
    assert "safe-agent/1.0" in blob
    assert "this-is-safe" in blob
    assert scrubbed["request"]["method"] == "POST"


def test_request_body_cookies_env_and_query_are_removed() -> None:
    scrubbed = obs.before_send(_event(), {})
    req = scrubbed["request"]
    assert "data" not in req and "cookies" not in req and "env" not in req
    assert req["query_string"] == ""
    assert "?" not in req["url"]


def test_scrubbers_tolerate_non_dict_and_missing_fields() -> None:
    assert obs.before_send(None, {}) is None
    assert obs.before_breadcrumb("not a dict", {}) == "not a dict"
    assert obs.before_send({}, {}) == {}


# --- init: disabled and inert without a DSN --------------------------------

def test_init_sentry_is_a_noop_without_a_dsn(monkeypatch) -> None:
    monkeypatch.setattr(settings, "sentry_dsn", "")
    monkeypatch.setattr(obs, "_initialized", False)

    def boom(*a, **k):
        raise AssertionError("sentry_sdk.init must not be called without a DSN")

    import sentry_sdk
    monkeypatch.setattr(sentry_sdk, "init", boom)
    assert obs.init_sentry() is False


def test_init_sentry_passes_privacy_options_when_a_dsn_is_set(monkeypatch) -> None:
    monkeypatch.setattr(settings, "sentry_dsn", "https://examplePublicKey@o0.ingest.sentry.io/0")
    monkeypatch.setattr(settings, "sentry_traces_sample_rate", 0.0)
    monkeypatch.setattr(obs, "_initialized", False)
    captured = {}

    def fake_init(**kwargs):
        captured.update(kwargs)

    import sentry_sdk
    monkeypatch.setattr(sentry_sdk, "init", fake_init)
    assert obs.init_sentry() is True
    assert captured["send_default_pii"] is False
    assert captured["max_request_body_size"] == "never"
    assert captured["include_local_variables"] is False
    assert captured["trace_propagation_targets"] == []
    assert captured["auto_session_tracking"] is False
    assert captured["before_send"] is obs.before_send
    assert captured["before_send_transaction"] is obs.before_send_transaction
    assert captured["before_breadcrumb"] is obs.before_breadcrumb


# --- provider errors: the upstream body never becomes a message -------------
#
# The scrubber above cannot recognise a prompt or SQL in free text, so the fix
# is at the source: a provider's error body (which can echo the request) never
# enters the exception message that becomes a log line, a breadcrumb and a
# chained exception. These bodies plant a prompt, generated SQL and a key.

PROMPT_S = "PROVIDER-PROMPT-SENTINEL-c01"
SQL_S = "provider_sql_sentinel_c02"
KEY_S = "AIzaPROVIDERKEYSENTINELc03xxxxxxxxxxxx"
_ECHO = f"Invalid value at 'contents' ({PROMPT_S}); SELECT {SQL_S} FROM t; key={KEY_S}"


def _leaks(text: str) -> list[str]:
    return [s for s in (PROMPT_S, SQL_S, KEY_S) if s in text]


def _mock_http(monkeypatch, handler) -> None:
    real = httpx.AsyncClient

    def client(*a, **kw):
        kw["transport"] = httpx.MockTransport(handler)
        return real(*a, **kw)

    monkeypatch.setattr(P.httpx, "AsyncClient", client)


def _gemini_error(status: int, code: str = "INVALID_ARGUMENT") -> httpx.Response:
    return httpx.Response(status, json={"error": {"code": status, "status": code,
                                                  "message": _ECHO}})


@pytest.mark.parametrize("provider_cls, response, expected", [
    (P.GeminiProvider, _gemini_error(400), "400 INVALID_ARGUMENT"),
    (P.GeminiProvider, _gemini_error(403, "PERMISSION_DENIED"), "403 PERMISSION_DENIED"),
    (P.GeminiProvider, _gemini_error(503, "UNAVAILABLE"), "503 UNAVAILABLE"),   # whole chain
    (P.GeminiProvider, httpx.Response(500, text=_ECHO), "returned 500"),        # non-JSON body
    # A "code" that is prose, not a token, is dropped rather than echoed.
    (P.GeminiProvider, httpx.Response(400, json={"error": {"status": _ECHO}}), "returned 400"),
    # 200 with no text part: the payload (model output -- maybe the SQL) is not quoted.
    (P.GeminiProvider, httpx.Response(200, json={"candidates": [
        {"finishReason": "STOP", "content": {"parts": []}}], "sql": _ECHO}), "response shape"),
    (P.GroqProvider, httpx.Response(400, json={"error": {
        "message": _ECHO, "type": "invalid_request_error", "code": None}}),
     "400 invalid_request_error"),
])
def test_provider_error_message_carries_status_and_code_not_the_body(
        monkeypatch, provider_cls, response, expected) -> None:
    _mock_http(monkeypatch, lambda req: response)
    p = provider_cls(api_key="test-key", use_cache=False)
    with pytest.raises(P.LLMError) as exc:
        asyncio.run(p._call(f"question {PROMPT_S}", "", 0.0))
    msg = str(exc.value)
    assert not _leaks(msg), f"provider body leaked into the exception: {msg}"
    assert expected in msg, msg            # still diagnosable


def _failing_gemini(monkeypatch) -> P.GeminiProvider:
    _mock_http(monkeypatch, lambda req: _gemini_error(400))
    p = P.GeminiProvider(api_key="test-key", use_cache=False)
    monkeypatch.setattr(limits, "get_provider", lambda name=None, use_cache=True: p)
    return p


def test_a_provider_failure_logs_a_summary_and_the_response_is_unchanged(
        monkeypatch, caplog) -> None:
    from rrip.api.main import app

    _failing_gemini(monkeypatch)
    caplog.set_level(logging.WARNING)
    r = TestClient(app).post("/api/v1/ai/narrate",
                             json={"data": {"revenue": 1}, "question": f"Explain {PROMPT_S}"})

    # The response is what it was before this change.
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False and body["narrative"] is None
    assert body["rejected_reason"] == ai_routes._PROVIDER_DOWN
    assert not _leaks(r.text)

    # The log line: provider, model, status and code -- and nothing planted.
    logged = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "gemini" in logged and "400 INVALID_ARGUMENT" in logged, logged
    assert not _leaks(logged), f"provider body leaked into a log record: {logged}"


# Run in a subprocess: sentry_sdk.init() patches logging and httpx process-wide,
# which must not bleed into the rest of the suite. A capturing transport stands
# in for the network; the DSN is syntactically valid and never contacted.
_SENTRY_PROBE = r'''
import asyncio, json, sys
import httpx, sentry_sdk
from sentry_sdk.transport import Transport
from rrip.ai import provider as P
from rrip.api import ai_routes, limits, observability as obs
from rrip.api.models import NarrateRequest
from rrip.config import settings

S = json.loads(sys.argv[1])

class Capture(Transport):
    def __init__(self):
        super().__init__()
        self.items = []
    def capture_envelope(self, envelope):
        self.items += [(i.type, i.payload.json) for i in envelope.items if i.payload.json]

cap = Capture()
_init = sentry_sdk.init
sentry_sdk.init = lambda **kw: _init(**kw, transport=cap)
settings.sentry_dsn = "https://publickey@o0.ingest.sentry.io/0"
assert obs.init_sentry()

def upstream(req):
    return httpx.Response(400, json={"error": {"code": 400, "status": "INVALID_ARGUMENT",
                                               "message": S["echo"]}})
_real = httpx.AsyncClient
P.httpx.AsyncClient = lambda *a, **kw: _real(*a, transport=httpx.MockTransport(upstream), **kw)
p = P.GeminiProvider(api_key=S["key"], use_cache=False)
limits.get_provider = lambda name=None, use_cache=True: p

async def main():
    # One scope: the route's log line and the outbound call become breadcrumbs,
    # then the provider exception itself is reported, carrying them.
    with sentry_sdk.isolation_scope():
        out = await ai_routes.narrate_endpoint(
            NarrateRequest(data={"revenue": 1}, question=S["question"]), provider=None)
        try:
            await p.complete(S["question"])
        except P.LLMError as exc:
            sentry_sdk.capture_exception(exc)
    return out

out = asyncio.run(main())
sentry_sdk.flush()
print(json.dumps({"response": out, "items": cap.items}, default=str))
'''


def test_sentry_event_and_breadcrumbs_from_a_provider_failure_carry_no_prompt_sql_or_key():
    probe = {"question": f"Explain {PROMPT_S}", "key": KEY_S, "echo": _ECHO}
    env = {k: v for k, v in os.environ.items() if not k.startswith("SENTRY_")}
    r = subprocess.run([sys.executable, "-c", _SENTRY_PROBE, json.dumps(probe)],
                       capture_output=True, text=True, timeout=120, env=env,
                       cwd=PROJECT_ROOT)
    assert r.returncode == 0, r.stderr[-3000:]
    out = json.loads(r.stdout.strip().splitlines()[-1])

    events = [payload for kind, payload in out["items"] if kind == "event"]
    assert len(events) == 1, out["items"]
    blob = json.dumps(events)
    assert not _leaks(blob), f"planted values reached the Sentry event: {_leaks(blob)}"

    ev = events[0]
    reported = ev["exception"]["values"][-1]
    assert reported["type"] == "NonRetryable"                 # safe diagnostics kept
    assert "400 INVALID_ARGUMENT" in reported["value"]
    crumbs = ev["breadcrumbs"]["values"]
    assert any("400 INVALID_ARGUMENT" in (c.get("message") or "") for c in crumbs), crumbs
    assert any(c.get("category") == "httplib" for c in crumbs), crumbs
    assert out["response"]["rejected_reason"] == ai_routes._PROVIDER_DOWN
