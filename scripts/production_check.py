"""Daily read-only check of the live site.

    python scripts/production_check.py

Prints one line per check and exits non-zero if any check fails. It is run
every day by .github/workflows/production-check.yml, which opens an issue when
it fails.

WHAT IT MAY DO

Plain GET requests, and nothing else. It never asks a question of the Ask page
and never calls an /ai/ route, so it cannot spend a model call or touch the
daily LLM cap. It reads no database, holds no secret and needs no credentials:
everything it fetches is what an anonymous visitor's browser fetches.

It uses the standard library only, so the workflow needs no install step and
the check cannot break because a dependency did.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

# --- configuration -------------------------------------------------------------
#
# The site, and everything the check expects of it, in one place. When a page
# heading or the published tier changes on purpose, this block is what changes
# with it; tests/test_production_check.py ties these values to the sources they
# describe, so a mismatch is caught in CI rather than by the daily run.

SITE = "https://retail-revenue-intelligence-flame.vercel.app"

TIMEOUT_SECONDS = 20
NETWORK_RETRIES = 1          # one retry, on a network error only -- never on a bad status
RETRY_PAUSE_SECONDS = 3
USER_AGENT = "rrip-production-check (+github.com/Kodishalathrilok/retail-revenue-intelligence)"

# Page -> the heading it must contain.
PAGES = {
    "/": "What changed, what is likely next, and what caused it",
    "/query": "Ask the data",
    "/forecast": "Next-week revenue forecast",
    "/causal": "Did the campaign change spending?",
}

HEALTH_PATH = "/health"
EXPECTED_TIER = "published"
EXPECTED_PUBLISHED_TABLES = 23

FORECAST_PATH = "/api/v1/forecast?department=GROCERY"
FORECAST_SUMMARY_PATH = "/api/v1/forecast/summary"
EXPECTED_HORIZON_WEEKS = 1               # forecasting is one week ahead, only
EXPECTED_DEPLOYED_KIND = "baseline"      # the 4-week average is live; the model is not

# Estimator validation needs the scientific stack, which production does not
# install. A clean 503 LOCAL_ONLY is the designed answer; a 200 would mean the
# deployment changed, and anything else means the route broke.
VALIDATION_PATH = "/api/v1/causal/validation"
EXPECTED_VALIDATION_STATUS = 503
EXPECTED_VALIDATION_ERROR = "LOCAL_ONLY"

CAUSAL_PAGE = "/causal"
CAUSAL_ANALYSIS_PATH = "/api/v1/causal/analysis/{campaign_id}"
CAUSAL_CAMPAIGNS = (26, 18)              # the two campaigns the Causal page shows
# The API fields the Causal page prints as it receives them. It builds every
# other sentence itself, and those live in the page's scripts, which are
# scanned too. Fields the page does not print (for one, `interpretation`) are
# deliberately left out: the check is about what a visitor is shown.
CAUSAL_RENDERED_FIELDS = ("warnings",)
# Never shown, in any form: matched case-insensitively, with hyphens and runs
# of whitespace treated as one space, so "Parallel-trends holds" is caught too.
FORBIDDEN_PHRASES = ("parallel trends hold", "parallel trends proven")

# --- end of configuration ------------------------------------------------------

SCRIPT_SRC = re.compile(r"""["'](/_next/static/chunks/[A-Za-z0-9/_.\-]+\.js)""")


@dataclass(frozen=True)
class Response:
    """What a GET came back with. `status` is 0 when no answer arrived at all."""

    status: int
    body: str = ""
    error: str = ""

    def json(self) -> object:
        try:
            return json.loads(self.body)
        except ValueError:
            return None


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str

    def line(self) -> str:
        return f"{'PASS' if self.ok else 'FAIL'}  {self.name}: {self.detail}"


Fetch = Callable[[str], Response]


def fetch(path: str, site: str = SITE) -> Response:
    """GET one path. Retries once on a network error; a bad status is an answer."""
    request = urllib.request.Request(site.rstrip("/") + path, method="GET",
                                     headers={"User-Agent": USER_AGENT})
    problem = ""
    for attempt in range(NETWORK_RETRIES + 1):
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as reply:
                return Response(reply.status, reply.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            # The server answered, with a status that is not 2xx. Not retried:
            # for one route here a 503 is the expected answer.
            return Response(exc.code, exc.read().decode("utf-8", "replace"))
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            problem = f"{type(exc).__name__}: {getattr(exc, 'reason', exc)}"
            if attempt < NETWORK_RETRIES:
                time.sleep(RETRY_PAUSE_SECONDS)
    return Response(0, "", problem)


def _unanswered(response: Response) -> str:
    return f"no answer ({response.error or 'unknown network error'})"


# --- the checks: each one judges responses it is given, and fetches nothing ----

def check_page(path: str, heading: str, response: Response) -> Check:
    name = f"page {path}"
    if response.status == 0:
        return Check(name, False, _unanswered(response))
    if response.status != 200:
        return Check(name, False, f"status {response.status}, expected 200")
    if heading not in html.unescape(response.body):
        return Check(name, False, f"status 200 but the heading is missing: {heading!r}")
    return Check(name, True, f"200, heading present: {heading!r}")


def check_health(response: Response) -> Check:
    name = f"health {HEALTH_PATH}"
    if response.status == 0:
        return Check(name, False, _unanswered(response))
    data = response.json()
    if response.status != 200 or not isinstance(data, dict):
        return Check(name, False, f"status {response.status}, expected 200 with a JSON body")
    tier, tables = data.get("tier"), data.get("published_tables")
    if tier != EXPECTED_TIER or tables != EXPECTED_PUBLISHED_TABLES:
        return Check(name, False,
                     f"tier {tier!r} with {tables!r} tables, expected {EXPECTED_TIER!r} "
                     f"with {EXPECTED_PUBLISHED_TABLES}")
    return Check(name, True, f"tier {tier}, {tables} published tables")


def _number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def check_forecast(response: Response) -> Check:
    """The forecast payload: one week ahead, a forecast inside its range, and
    the baseline -- not the challenger model -- as the predictor that is live."""
    name = "forecast payload"
    if response.status == 0:
        return Check(name, False, _unanswered(response))
    data = response.json()
    if response.status != 200 or not isinstance(data, dict):
        return Check(name, False, f"status {response.status}, expected 200 with a JSON body")

    horizon = data.get("horizon_weeks")
    if horizon != EXPECTED_HORIZON_WEEKS:
        return Check(name, False,
                     f"horizon_weeks is {horizon!r}, expected {EXPECTED_HORIZON_WEEKS}")
    forecast, low, high = (data.get(k) for k in ("prediction", "lower_bound", "upper_bound"))
    if not all(_number(v) for v in (forecast, low, high)):
        return Check(name, False, "forecast or range is missing or not a number")
    if not (low <= forecast <= high) or forecast <= 0:
        return Check(name, False, f"forecast {forecast} is not inside its range {low} to {high}")

    baseline = data.get("baseline") or {}
    challenger = data.get("challenger_model") or {}
    if challenger.get("deployed") is not False:
        return Check(name, False, "the challenger model is not marked as not deployed")
    if baseline.get("name") != data.get("model_type") or baseline.get("prediction") != forecast:
        return Check(name, False,
                     f"the live forecast is not the baseline's: model_type "
                     f"{data.get('model_type')!r}, baseline {baseline.get('name')!r}")
    return Check(name, True,
                 f"{horizon} week ahead, forecast {forecast:,.0f} in range {low:,.0f} to "
                 f"{high:,.0f}, baseline {baseline.get('name')} is live")


def check_forecast_summary(response: Response) -> Check:
    name = "forecast model card"
    if response.status == 0:
        return Check(name, False, _unanswered(response))
    data = response.json()
    if response.status != 200 or not isinstance(data, dict):
        return Check(name, False, f"status {response.status}, expected 200 with a JSON body")
    kind, horizons = data.get("deployed_kind"), data.get("horizon_weeks")
    if kind != EXPECTED_DEPLOYED_KIND:
        return Check(name, False,
                     f"deployed_kind is {kind!r}, expected {EXPECTED_DEPLOYED_KIND!r}")
    if horizons != [EXPECTED_HORIZON_WEEKS]:
        return Check(name, False,
                     f"horizon_weeks is {horizons!r}, expected [{EXPECTED_HORIZON_WEEKS}]")
    return Check(name, True, f"deployed_kind {kind}, horizons {horizons}")


def check_validation(response: Response) -> Check:
    name = f"local-only route {VALIDATION_PATH}"
    if response.status == 0:
        return Check(name, False, _unanswered(response))
    if response.status != EXPECTED_VALIDATION_STATUS:
        return Check(name, False,
                     f"status {response.status}, expected {EXPECTED_VALIDATION_STATUS} "
                     f"{EXPECTED_VALIDATION_ERROR}: investigate")
    data = response.json()
    detail = data.get("detail") if isinstance(data, dict) else None
    error = detail.get("error") if isinstance(detail, dict) else None
    if error != EXPECTED_VALIDATION_ERROR:
        return Check(name, False,
                     f"status {response.status} but the error is {error!r}, expected "
                     f"{EXPECTED_VALIDATION_ERROR!r}")
    return Check(name, True, f"{response.status} {error}, as designed")


def forbidden_in(text: str) -> list[str]:
    """The forbidden phrases that appear in `text`, however they are spaced."""
    flat = re.sub(r"[\s\-]+", " ", html.unescape(text)).lower()
    return [phrase for phrase in FORBIDDEN_PHRASES if phrase in flat]


def check_wording(name: str, texts: dict[str, str]) -> Check:
    """`texts` maps a label (a file, a campaign) to the text shown from it."""
    found = [f"{label}: {phrase!r}" for label, text in texts.items()
             for phrase in forbidden_in(text)]
    if found:
        return Check(name, False, "forbidden phrase shown -- " + "; ".join(found))
    return Check(name, True, f"no forbidden phrase in {len(texts)} source(s)")


def script_paths(page_html: str) -> list[str]:
    """The script files a page loads: where the sentences it builds itself live."""
    return sorted(set(SCRIPT_SRC.findall(page_html)))


def rendered_text(analysis: object) -> str:
    """The text of the API fields the Causal page prints as it receives them."""
    if not isinstance(analysis, dict):
        return ""
    return " ".join(json.dumps(analysis.get(field, "")) for field in CAUSAL_RENDERED_FIELDS)


# --- running them --------------------------------------------------------------

def run(get: Fetch) -> list[Check]:
    checks: list[Check] = []

    pages = {path: get(path) for path in PAGES}
    checks += [check_page(path, heading, pages[path]) for path, heading in PAGES.items()]

    checks.append(check_health(get(HEALTH_PATH)))
    checks.append(check_forecast(get(FORECAST_PATH)))
    checks.append(check_forecast_summary(get(FORECAST_SUMMARY_PATH)))
    checks.append(check_validation(get(VALIDATION_PATH)))

    # What the Causal page shows comes from three places: its HTML, the
    # scripts that build its sentences, and the API fields it prints as given.
    causal = pages[CAUSAL_PAGE]
    checks.append(check_wording("causal page html", {CAUSAL_PAGE: causal.body}))

    scripts = script_paths(causal.body)
    replies = {path: get(path) for path in scripts}
    unread = [path for path, reply in replies.items() if reply.status != 200]
    if not scripts:
        checks.append(Check("causal page scripts", False, "the page lists no script files"))
    elif unread:
        checks.append(Check("causal page scripts", False,
                            f"could not read {len(unread)} of {len(scripts)}: {unread[0]}"))
    else:
        checks.append(check_wording("causal page scripts",
                                    {path: reply.body for path, reply in replies.items()}))

    for campaign in CAUSAL_CAMPAIGNS:
        name = f"causal campaign {campaign} shown text"
        reply = get(CAUSAL_ANALYSIS_PATH.format(campaign_id=campaign))
        if reply.status == 0:
            checks.append(Check(name, False, _unanswered(reply)))
        elif reply.status != 200 or not isinstance(reply.json(), dict):
            checks.append(Check(name, False,
                                f"status {reply.status}, expected 200 with a JSON body"))
        else:
            checks.append(check_wording(name, {f"campaign {campaign}":
                                               rendered_text(reply.json())}))
    return checks


def main(argv: list[str] | None = None, get: Fetch | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only check of the live site.")
    parser.add_argument("--site", default=SITE,
                        help="Site to check. Defaults to production.")
    args = parser.parse_args(argv)

    checks = run(get or (lambda path: fetch(path, args.site)))
    for check in checks:
        print(check.line())
    failed = [c for c in checks if not c.ok]
    print(f"{len(checks)} checks on {args.site}: "
          f"{'all passed' if not failed else f'{len(failed)} FAILED'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
