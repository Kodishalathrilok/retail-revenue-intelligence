"""The daily production check, tested on fixed sample responses. No network.

scripts/production_check.py judges the live site from plain GETs. These tests
feed its check functions canned responses -- a healthy site, then one thing
wrong at a time -- and tie the script's config block to the sources it
describes, so a deliberate change to a heading or to the published tier fails
here, in CI, instead of opening an issue the next morning.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "production_check.py"
WORKFLOW = ROOT / ".github" / "workflows" / "production-check.yml"

_spec = importlib.util.spec_from_file_location("production_check", SCRIPT)
pc = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = pc          # dataclasses resolve annotations through sys.modules
_spec.loader.exec_module(pc)

Response = pc.Response

CHUNK = "/_next/static/chunks/app/causal/page-abc123.js"
SHARED = "/_next/static/chunks/995-def456.js"


def _page(heading: str, scripts: tuple[str, ...] = ()) -> Response:
    tags = "".join(f'<script src="{s}" async=""></script>' for s in scripts)
    return Response(200, f"<html><body><h1>{heading}</h1>{tags}</body></html>")


def _json(status: int, payload: object) -> Response:
    return Response(status, json.dumps(payload))


FORECAST = {
    "horizon_weeks": 1, "prediction": 49031.5, "lower_bound": 28691.26,
    "upper_bound": 69807.09, "model_type": "trailing_mean_4",
    "baseline": {"name": "trailing_mean_4", "prediction": 49031.5},
    "challenger_model": {"name": "hgb(lr=0.02,leaves=7,min_leaf=40)",
                         "prediction": 48194.26, "deployed": False},
}
ANALYSIS = {
    "campaign_id": 26, "did_estimate": 1.5068,
    "warnings": ["the DiD estimate is not statistically distinguishable from zero (p = 0.444)."],
    # Returned by the API, never printed by the page.
    "interpretation": {"did": "Valid only if parallel trends holds."},
    "parallel_trends": {"verdict": "The pre-period test did not reject parallel trends."},
}


def healthy() -> dict[str, Response]:
    """Every response the check asks for, as a healthy production returns it."""
    site = {path: _page(heading) for path, heading in pc.PAGES.items()}
    site[pc.CAUSAL_PAGE] = _page(pc.PAGES[pc.CAUSAL_PAGE], (CHUNK, SHARED))
    site[CHUNK] = Response(200, 'children:"Assumptions and limitations"')
    site[SHARED] = Response(200, '"The test did not reject differential pre-treatment trends"')
    site[pc.HEALTH_PATH] = _json(200, {"status": "ok", "tier": "published",
                                       "published_tables": 23})
    site[pc.FORECAST_PATH] = _json(200, FORECAST)
    site[pc.FORECAST_SUMMARY_PATH] = _json(200, {"deployed_kind": "baseline",
                                                 "horizon_weeks": [1]})
    site[pc.VALIDATION_PATH] = _json(503, {"detail": {"error": "LOCAL_ONLY", "message": "m"}})
    for campaign in pc.CAUSAL_CAMPAIGNS:
        site[pc.CAUSAL_ANALYSIS_PATH.format(campaign_id=campaign)] = _json(200, ANALYSIS)
    return site


def run(site: dict[str, Response]) -> list:
    return pc.run(lambda path: site.get(path, Response(404, "not found")))


def failed(site: dict[str, Response]) -> list[str]:
    return [c.name for c in run(site) if not c.ok]


# --- a healthy site ---------------------------------------------------------------

def test_a_healthy_site_passes_every_check_with_one_line_each() -> None:
    checks = run(healthy())
    assert [c.ok for c in checks] == [True] * len(checks)
    # 4 pages, health, forecast, model card, local-only route, causal html,
    # causal scripts, and one line per campaign.
    assert len(checks) == len(pc.PAGES) + 6 + len(pc.CAUSAL_CAMPAIGNS)
    assert len({c.name for c in checks}) == len(checks)
    assert all(c.line().startswith("PASS  ") and "\n" not in c.line() for c in checks)


def test_main_prints_a_line_per_check_and_exits_zero_or_one(capsys) -> None:
    site = healthy()
    assert pc.main([], get=lambda path: site.get(path, Response(404))) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert all(line.startswith("PASS  ") for line in lines[:-1]) and "all passed" in lines[-1]

    site[pc.HEALTH_PATH] = _json(200, {"tier": "local"})
    assert pc.main([], get=lambda path: site.get(path, Response(404))) == 1
    out = capsys.readouterr().out
    assert out.count("\nFAIL  ") + out.startswith("FAIL  ") == 1 and "1 FAILED" in out


# --- one thing wrong at a time ------------------------------------------------------

def test_a_page_fails_on_a_bad_status_or_a_missing_heading() -> None:
    site = healthy()
    site["/forecast"] = Response(500, "Internal Server Error")
    assert failed(site) == ["page /forecast"]

    site = healthy()
    site["/query"] = _page("Something else")
    assert failed(site) == ["page /query"]
    # An escaped heading in the HTML still counts as present.
    site["/query"] = Response(200, "<h1>Ask the data</h1>".replace("the", "th&#101;"))
    assert failed(site) == []


def test_health_fails_on_the_wrong_tier_or_table_count() -> None:
    for payload in ({"tier": "local", "published_tables": 23},
                    {"tier": "published", "published_tables": 22},
                    {"tier": "published"}):
        site = healthy()
        site[pc.HEALTH_PATH] = _json(200, payload)
        assert failed(site) == [f"health {pc.HEALTH_PATH}"], payload
    site = healthy()
    site[pc.HEALTH_PATH] = Response(200, "<html>not json</html>")
    assert failed(site) == [f"health {pc.HEALTH_PATH}"]


@pytest.mark.parametrize("change,why", [
    ({"horizon_weeks": 4}, "horizon_weeks is 4"),
    ({"upper_bound": 40000.0}, "not inside its range"),
    ({"prediction": None}, "missing or not a number"),
    ({"challenger_model": {"prediction": 48194.26, "deployed": True}},
     "not marked as not deployed"),
    ({"challenger_model": {}}, "not marked as not deployed"),
    ({"model_type": "hgb"}, "not the baseline's"),
    ({"baseline": {"name": "trailing_mean_4", "prediction": 1.0}}, "not the baseline's"),
])
def test_the_forecast_payload_must_be_one_week_ahead_in_range_and_the_baseline(
        change: dict, why: str) -> None:
    site = healthy()
    site[pc.FORECAST_PATH] = _json(200, {**FORECAST, **change})
    checks = [c for c in run(site) if not c.ok]
    assert [c.name for c in checks] == ["forecast payload"] and why in checks[0].detail


def test_the_model_card_must_say_the_baseline_is_deployed_one_week_ahead() -> None:
    for payload in ({"deployed_kind": "model", "horizon_weeks": [1]},
                    {"deployed_kind": "baseline", "horizon_weeks": [1, 4]}):
        site = healthy()
        site[pc.FORECAST_SUMMARY_PATH] = _json(200, payload)
        assert failed(site) == ["forecast model card"], payload


def test_the_local_only_route_must_answer_503_local_only_and_nothing_else() -> None:
    name = f"local-only route {pc.VALIDATION_PATH}"
    for reply in (_json(200, {"items": []}),                       # a 200 is a failure too
                  _json(500, {"detail": "Internal Server Error"}),
                  _json(404, {"detail": "Not Found"}),
                  _json(503, {"detail": {"error": "MODEL_UNAVAILABLE"}}),
                  Response(503, "Service Unavailable")):
        site = healthy()
        site[pc.VALIDATION_PATH] = reply
        checks = [c for c in run(site) if not c.ok]
        assert [c.name for c in checks] == [name], reply
    site = healthy()
    site[pc.VALIDATION_PATH] = _json(200, {"items": []})
    assert "investigate" in next(c for c in run(site) if not c.ok).detail


@pytest.mark.parametrize("text", [
    "Parallel trends hold for this campaign.",
    "the parallel-trends   holds",
    "PARALLEL TRENDS PROVEN",
    "parallel&nbsp;trends hold",
])
def test_the_forbidden_phrase_is_caught_wherever_the_causal_page_shows_it(text: str) -> None:
    assert pc.forbidden_in(text)

    site = healthy()
    site[pc.CAUSAL_PAGE] = Response(200, site[pc.CAUSAL_PAGE].body + text)
    assert failed(site) == ["causal page html"]

    site = healthy()
    site[SHARED] = Response(200, f'children:"{text}"')
    assert failed(site) == ["causal page scripts"]

    site = healthy()
    path = pc.CAUSAL_ANALYSIS_PATH.format(campaign_id=18)
    site[path] = _json(200, {**ANALYSIS, "warnings": [text]})
    assert failed(site) == ["causal campaign 18 shown text"]


def test_wording_the_page_does_not_show_is_not_a_failure() -> None:
    # The API's `interpretation` field says "valid only if parallel trends
    # holds", and the stored verdict says "did not reject parallel trends".
    # The Causal page prints neither, and the healthy sample carries both.
    assert pc.forbidden_in(json.dumps(ANALYSIS["interpretation"]))
    assert failed(healthy()) == []
    for phrase in ("did not reject differential pre-treatment trends",
                   "the parallel-trends assumption is violated",
                   "PARALLEL TRENDS VIOLATED"):
        assert pc.forbidden_in(phrase) == []


def test_a_page_script_that_cannot_be_read_is_a_failure_not_a_pass() -> None:
    site = healthy()
    site[CHUNK] = Response(404, "")
    assert failed(site) == ["causal page scripts"]
    site = healthy()
    site[pc.CAUSAL_PAGE] = _page(pc.PAGES[pc.CAUSAL_PAGE])          # lists no scripts
    assert failed(site) == ["causal page scripts"]


def test_a_request_that_gets_no_answer_fails_its_check_and_says_why() -> None:
    site = healthy()
    site["/"] = Response(0, "", "URLError: timed out")
    site[pc.FORECAST_PATH] = Response(0, "", "URLError: timed out")
    checks = [c for c in run(site) if not c.ok]
    assert [c.name for c in checks] == ["page /", "forecast payload"]
    assert all("no answer (URLError: timed out)" in c.detail for c in checks)


# --- the fetcher: GET only, 20 s, one retry on a network error -------------------

class _Reply:
    status = 200

    def __init__(self, body: bytes = b"ok") -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        return None


def _patch(monkeypatch, outcomes: list) -> list:
    """Make urlopen return or raise the given outcomes in turn; record calls."""
    calls: list = []

    def urlopen(request, timeout):
        calls.append((request.get_method(), request.full_url, timeout))
        outcome = outcomes[len(calls) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(pc.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(pc.time, "sleep", lambda seconds: None)
    return calls


def test_fetch_is_a_get_with_a_20_second_timeout(monkeypatch) -> None:
    calls = _patch(monkeypatch, [_Reply(b"hello")])
    assert pc.fetch("/health", "https://example.test/") == Response(200, "hello")
    assert calls == [("GET", "https://example.test/health", 20)]
    assert pc.TIMEOUT_SECONDS == 20


def test_fetch_retries_once_on_a_network_error_then_gives_up(monkeypatch) -> None:
    down = urllib.error.URLError("timed out")
    calls = _patch(monkeypatch, [down, _Reply(b"second time")])
    assert pc.fetch("/", "https://example.test") == Response(200, "second time")
    assert len(calls) == 2

    calls = _patch(monkeypatch, [down, down, _Reply()])
    reply = pc.fetch("/", "https://example.test")
    assert (reply.status, reply.body) == (0, "") and "timed out" in reply.error
    assert len(calls) == 2 and pc.NETWORK_RETRIES == 1


def test_fetch_does_not_retry_a_bad_status(monkeypatch) -> None:
    # A 503 is an answer -- and for one route, the right one.
    error = urllib.error.HTTPError("https://example.test/x", 503, "Service Unavailable", {},
                                   None)
    error.read = lambda: b'{"detail": {"error": "LOCAL_ONLY"}}'
    calls = _patch(monkeypatch, [error, _Reply()])
    reply = pc.fetch("/x", "https://example.test")
    assert reply.status == 503 and "LOCAL_ONLY" in reply.body and len(calls) == 1


# --- the script stays read-only, and its config matches what it describes ----------

def test_the_script_only_ever_sends_get_requests_and_never_calls_a_model_route() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    code = re.sub(r'""".*?"""', "", source, flags=re.S)             # docstrings name /ai/
    code = re.sub(r"(?m)#.*$", "", code)
    assert 'method="GET"' in code and "POST" not in code and "data=" not in code
    assert "/ai/" not in code and "/ask" not in code
    paths = [pc.HEALTH_PATH, pc.FORECAST_PATH, pc.FORECAST_SUMMARY_PATH, pc.VALIDATION_PATH,
             pc.CAUSAL_ANALYSIS_PATH, *pc.PAGES]
    assert not any("/ai" in p for p in paths)
    # Standard library only: the workflow installs nothing.
    imported = set(re.findall(r"(?m)^(?:from|import) ([a-z_]+)", source))
    assert imported <= {"__future__", "argparse", "html", "json", "re", "sys", "time",
                        "urllib", "collections", "dataclasses"}


def test_the_config_sits_in_one_block_at_the_top_of_the_script() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    start, end = source.index("# --- configuration"), source.index("# --- end of configuration")
    block = source[start:end]
    for name in ("SITE", "TIMEOUT_SECONDS", "PAGES", "EXPECTED_TIER", "EXPECTED_PUBLISHED_TABLES",
                 "FORECAST_PATH", "EXPECTED_HORIZON_WEEKS", "EXPECTED_DEPLOYED_KIND",
                 "VALIDATION_PATH", "EXPECTED_VALIDATION_STATUS", "EXPECTED_VALIDATION_ERROR",
                 "CAUSAL_CAMPAIGNS", "CAUSAL_RENDERED_FIELDS", "FORBIDDEN_PHRASES"):
        assert re.search(rf"(?m)^{name} = ", block), f"{name} is not in the config block"
    assert "def " not in source[:start] and pc.SITE.startswith("https://")
    # Nothing below the block restates the site or a heading.
    assert "vercel.app" not in source[end:]
    assert not any(heading in source[end:] for heading in pc.PAGES.values())


def test_the_expected_headings_are_the_ones_the_pages_render() -> None:
    files = {"/": "app/page.tsx", "/query": "app/query/page.tsx",
             "/forecast": "app/forecast/page.tsx", "/causal": "app/causal/page.tsx"}
    assert set(pc.PAGES) == set(files)
    for path, rel in files.items():
        page = (ROOT / "frontend" / rel).read_text(encoding="utf-8")
        title = re.search(r'<PageHeader[^>]*?title="([^"]+)"', page, re.S)
        assert title and title.group(1) == pc.PAGES[path], path


def test_the_expected_values_are_the_ones_the_project_records() -> None:
    from rrip.api import causal_routes
    from rrip.forecast.contract import HORIZON_WEEKS

    tier = json.loads((ROOT / "reports/eval/published-tier.json").read_text(encoding="utf-8"))
    assert pc.EXPECTED_PUBLISHED_TABLES == tier["tables"]
    metadata = json.loads((ROOT / "models/forecast/metadata.json").read_text(encoding="utf-8"))
    assert pc.EXPECTED_DEPLOYED_KIND == metadata["deployment"]["deployed_kind"]
    assert pc.EXPECTED_HORIZON_WEEKS == HORIZON_WEEKS == 1
    routes = Path(causal_routes.__file__).read_text(encoding="utf-8")
    assert f'HTTPException({pc.EXPECTED_VALIDATION_STATUS}, {{' in routes
    assert f'"error": "{pc.EXPECTED_VALIDATION_ERROR}"' in routes
    causal_page = (ROOT / "frontend/app/causal/page.tsx").read_text(encoding="utf-8")
    for campaign in pc.CAUSAL_CAMPAIGNS:
        assert f"/api/v1/causal/analysis/{campaign}'" in causal_page
    # The fields scanned are the stored strings the page prints as received.
    assert pc.CAUSAL_RENDERED_FIELDS == ("warnings",)
    assert "a.warnings.map((w) => <li key={w}>{displayWarning(w)}</li>)" in causal_page
    assert not re.search(r"parallel_trends\.verdict|\.interpretation\b", causal_page)
    assert pc.FORBIDDEN_PHRASES == ("parallel trends hold", "parallel trends proven")


# --- the workflow -------------------------------------------------------------------

def test_the_workflow_runs_daily_and_by_hand_and_reports_through_an_issue() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert re.search(r"(?m)^\s+- cron: ['\"]\d+ \d+ \* \* \*['\"]", workflow), "not a daily cron"
    assert "workflow_dispatch:" in workflow
    assert "python scripts/production_check.py" in workflow
    assert 'TITLE: "Production check failed"' in workflow
    assert "gh issue comment" in workflow and "gh issue create" in workflow
    # The automatic token, scoped to issues; no stored secret.
    assert "issues: write" in workflow and "contents: read" in workflow
    assert "secrets." not in workflow and "${{ github.token }}" in workflow
    assert "pip install" not in workflow, "the script is standard library only"
