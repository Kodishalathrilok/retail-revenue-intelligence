"""Privacy-safe error monitoring (Sentry), optional and off by default.

WHY THIS IS MOSTLY SANITISATION, NOT SETUP

Calling sentry_sdk.init() is three lines. The work is making sure nothing
sensitive leaves the process. RRIP's public endpoints handle, in a single
request: a visitor's natural-language question, the SQL a model wrote from it,
the rows Postgres returned, and -- on the outbound side -- a Gemini API key in
an `x-goog-api-key` header. None of that may appear in a monitoring event.

So the SDK is initialised with the capture of bodies and local variables turned
OFF, and every event, transaction and breadcrumb is run through the scrubber
below before it is transmitted. The scrubber is a set of PURE functions
(scrub, scrub_event, before_send, before_send_transaction, before_breadcrumb)
tested directly in tests/test_observability.py with synthetic sentinel secrets
-- the privacy boundary is asserted, not assumed.

DISABLED WHEN UNCONFIGURED

No SENTRY_DSN -> init_sentry() does nothing and the app runs exactly as before.
Monitoring is opt-in; a missing DSN is the normal local/CI state.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

REDACTED = "[redacted]"

# Header and field names whose VALUE must never be transmitted. Compared after
# lower-casing and normalising '_' to '-', so "X-Goog-Api-Key", "x_goog_api_key"
# and "X-GOOG-API-KEY" all match. Covers auth/cookies, the AI provider keys, the
# database DSNs, and the request content RRIP's endpoints carry (prompt, SQL,
# the computed data, query parameters).
SENSITIVE_KEYS = frozenset({
    "authorization", "proxy-authorization", "cookie", "set-cookie",
    "x-goog-api-key", "x-api-key", "api-key", "apikey", "api_key",
    "password", "passwd", "pwd", "secret", "token", "access-token",
    "refresh-token", "session", "csrf", "x-csrf-token",
    "dsn", "connection-string", "conninfo",
    "sql", "query", "query-string", "querystring", "prompt", "question",
    "body", "request-body", "data", "params", "parameters",
    "statement", "db.statement", "db-statement",
    "gemini-api-key", "groq-api-key",
    "rrip-pg-dsn", "rrip-pg-password", "rrip-pg-readonly-dsn",
    "rrip-limiter-dsn", "rrip-publish-dsn", "sentry-dsn",
})

# String values that are secrets wherever they appear (even under an innocent
# key): credentials embedded in a URL/DSN, or a provider API key by its prefix.
_SECRET_VALUE_RE = re.compile(
    r"://[^/\s:@]+:[^/\s@]+@"          # scheme://user:password@host  (DSNs, URLs)
    r"|\bAIza[0-9A-Za-z_\-]{20,}"      # Google / Gemini API key
    r"|\bgsk_[0-9A-Za-z]{20,}"         # Groq API key
    r"|\bsk-[0-9A-Za-z]{20,}"          # generic sk- style key
)

_MAX_DEPTH = 25


def _norm_key(key: object) -> str:
    return str(key).strip().lower().replace("_", "-")


def _looks_like_secret(value: str) -> bool:
    return bool(_SECRET_VALUE_RE.search(value))


def scrub(value: object, _depth: int = 0) -> object:
    """Recursively redact sensitive content from an arbitrary event tree.

    A dict value is redacted when its key is sensitive; any string value is
    redacted when it looks like a credential. Lists and nested dicts are walked.
    Non-string scalars are left as they are -- a status code or a duration is
    safe and useful.
    """
    if _depth > _MAX_DEPTH:
        return REDACTED
    if isinstance(value, dict):
        out: dict = {}
        for k, v in value.items():
            if _norm_key(k) in SENSITIVE_KEYS:
                out[k] = REDACTED
            else:
                out[k] = scrub(v, _depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [scrub(v, _depth + 1) for v in value]
    if isinstance(value, str):
        return REDACTED if _looks_like_secret(value) else value
    return value


def _scrub_request(request: object) -> object:
    """The request interface needs removals the generic walk does not do:
    drop the body, cookies and server env outright, and strip any query string
    off the URL (defence in depth -- RRIP never puts secrets in a URL, but a
    captured one could still carry `?provider=` or similar)."""
    if not isinstance(request, dict):
        return request
    req = dict(request)
    for gone in ("data", "cookies", "env"):
        req.pop(gone, None)
    req["query_string"] = ""
    if isinstance(req.get("url"), str):
        req["url"] = req["url"].split("?", 1)[0]
    if isinstance(req.get("headers"), dict):
        req["headers"] = {
            k: (REDACTED if _norm_key(k) in SENSITIVE_KEYS else v)
            for k, v in req["headers"].items()
        }
    return req


def scrub_event(event: object) -> object:
    """Scrub a whole error or transaction event in place-safe fashion.

    Runs the recursive walk over everything the application or an integration
    attached (extra, contexts, tags, breadcrumbs, spans), then the
    request-specific removals. Also redacts a db span's description, which some
    integrations set to the raw SQL text.
    """
    if not isinstance(event, dict):
        return event
    event = scrub(event)  # type: ignore[assignment]
    if "request" in event:
        event["request"] = _scrub_request(event["request"])
    # Breadcrumbs embedded in the event: scrub() cleans their `data` by key, but
    # a query breadcrumb's MESSAGE is the raw SQL, so apply the same
    # category-aware redaction before_breadcrumb applies to live crumbs -- an
    # event callback does not always see what the breadcrumb callback saw.
    bc = event.get("breadcrumbs")
    crumbs = bc.get("values") if isinstance(bc, dict) else bc
    if isinstance(crumbs, list):
        for c in crumbs:
            if isinstance(c, dict) and _norm_key(c.get("category", "")) in ("query", "sql", "db"):
                c["message"] = REDACTED
    # db spans put the SQL text in `description`.
    spans = event.get("spans")
    if isinstance(spans, list):
        for span in spans:
            if isinstance(span, dict) and str(span.get("op", "")).startswith("db"):
                span["description"] = REDACTED
    return event


def before_send(event, _hint):
    """Error events."""
    return scrub_event(event)


def before_send_transaction(event, _hint):
    """Performance/transaction events (spans, operation names, metadata)."""
    return scrub_event(event)


def before_breadcrumb(crumb, _hint):
    """Breadcrumbs added by integrations (e.g. an HTTP call or a SQL query).

    The scrubber handles the data dict; a query/SQL breadcrumb's message IS the
    statement, so it is redacted explicitly while the category/level are kept.
    """
    if not isinstance(crumb, dict):
        return crumb
    crumb = scrub(crumb)  # type: ignore[assignment]
    if _norm_key(crumb.get("category", "")) in ("query", "sql", "db"):
        crumb["message"] = REDACTED
    return crumb


_initialized = False


def init_sentry() -> bool:
    """Initialise Sentry if SENTRY_DSN is set. Idempotent. Returns whether it
    is active. Reads config through the Settings object, like the rest of the
    app (pydantic-settings loads .env into Settings, not os.environ)."""
    global _initialized
    if _initialized:
        return True

    from rrip.config import settings

    dsn = settings.sentry_dsn
    if not dsn:
        logger.info("SENTRY_DSN not set -- error monitoring is disabled.")
        return False

    import sentry_sdk

    sentry_sdk.init(
        dsn=dsn,
        environment=settings.sentry_environment or settings.tier,
        # Privacy: no PII, no request bodies, no local-variable snapshots (which
        # could otherwise hold a prompt, generated SQL or a credential).
        send_default_pii=False,
        max_request_body_size="never",
        include_local_variables=False,
        # Tracing off unless deliberately dialed up; it is costly and captures
        # more surface to sanitise.
        traces_sample_rate=settings.sentry_traces_sample_rate,
        # The SDK's default (['.*']) adds sentry-trace/baggage headers to EVERY
        # outbound HTTP call -- i.e. to Gemini and Groq. Nothing downstream is
        # ours, so propagate to no one.
        trace_propagation_targets=[],
        # Release-health sessions (crash-free rate per release) are on by
        # default. Nothing here uses them, and a serverless function's "session"
        # is one request anyway. Off until there is a release dashboard to feed.
        auto_session_tracking=False,
        # The boundary: nothing leaves without passing the scrubbers above.
        before_send=before_send,
        before_send_transaction=before_send_transaction,
        before_breadcrumb=before_breadcrumb,
    )
    _initialized = True
    logger.info("Sentry error monitoring enabled (environment=%s, traces=%.3f).",
                settings.sentry_environment or settings.tier,
                settings.sentry_traces_sample_rate)
    return True
