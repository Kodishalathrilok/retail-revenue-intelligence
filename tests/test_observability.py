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

import json

from rrip.api import observability as obs
from rrip.config import settings

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
    assert captured["before_send"] is obs.before_send
    assert captured["before_send_transaction"] is obs.before_send_transaction
    assert captured["before_breadcrumb"] is obs.before_breadcrumb
