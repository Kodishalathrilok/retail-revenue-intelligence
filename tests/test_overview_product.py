"""The Overview is the product's home: it must stay honest, connected and consistent.

Four things are pinned here, each because it went wrong before or would be easy
to get wrong again:

* The Explain panels are static text about what the system does. A definition
  that names an endpoint the API does not serve, or a table its SQL does not
  read, is a made-up methodology -- so every entry is checked against the API.
* The weekly revenue chart used to draw the panel's enrolment ramp bare, which
  reads as revenue growth. The annotation, and the week it ends at, are pinned.
* The workflow links the three engines; a link to a page that does not exist is
  a broken product.
* The pages were painted in raw Tailwind slate/emerald/amber/red beside a
  tokenised shell. Raw palette classes and hex colours stay out.
"""

from __future__ import annotations

import inspect
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
API = ROOT / "src" / "rrip" / "api"
PAGES = {"/": "app/page.tsx", "/query": "app/query/page.tsx",
         "/forecast": "app/forecast/page.tsx", "/causal": "app/causal/page.tsx"}
UI_SOURCES = sorted(p for d in ("app", "components") for p in (FRONTEND / d).rglob("*.ts*"))


def _read(rel: str) -> str:
    return (FRONTEND / rel).read_text(encoding="utf-8")


def _code_only(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)       # block + JSX comments
    return re.sub(r"(?m)^\s*//.*$", "", text)                # whole-line comments


def _metrics() -> dict[str, dict]:
    """Each entry's endpoint, tables and text, parsed from lib/metrics.mjs."""
    text = _read("lib/metrics.mjs")
    out = {}
    for key, body in re.findall(r"(?m)^  (\w+): \{\n(.*?)^  \},", text, re.S):
        endpoint = re.search(r"endpoint: '([^']+)'", body)
        tables = re.search(r"tables: \[([^\]]+)\]", body)
        assert endpoint and tables, f"unparseable metric entry: {key}"
        out[key] = {"endpoint": endpoint.group(1), "body": body,
                    "tables": re.findall(r"'([^']+)'", tables.group(1))}
    return out


METRICS = _metrics()


# --- Explain: static, and true to the backend ------------------------------------

def test_the_parser_sees_every_explain_entry() -> None:
    assert {"revenue", "baskets", "households", "avg_basket", "units", "weekly_revenue",
            "flagged_weeks", "rfm_segments", "forecast", "campaign_effect"} <= set(METRICS)


@pytest.mark.parametrize("key", sorted(METRICS))
def test_every_explain_entry_names_an_endpoint_the_api_serves(key: str) -> None:
    from rrip.api.main import app

    assert METRICS[key]["endpoint"] in app.openapi()["paths"]


@pytest.mark.parametrize("key", sorted(METRICS))
def test_every_explain_entry_names_tables_the_api_reads(key: str) -> None:
    sql = "".join((API / f).read_text(encoding="utf-8")
                  for f in ("queries.py", "forecast_routes.py", "causal_routes.py"))
    missing = [t for t in METRICS[key]["tables"] if not re.search(rf"\b{t}\b", sql)]
    assert not missing, f"{key} cites tables the API never reads: {missing}"


def test_every_explained_metric_on_a_page_is_defined() -> None:
    used = set()
    for path in UI_SOURCES:
        text = path.read_text(encoding="utf-8")
        for group in re.findall(r"<Explain metrics=\{\[([^\]]+)\]\}", text):
            used |= set(re.findall(r"'(\w+)'", group))
    assert used, "no page uses <Explain>"
    assert used <= set(METRICS), f"undefined metrics: {used - set(METRICS)}"


def test_explain_is_opt_in_and_never_calls_a_model() -> None:
    code = _code_only(_read("components/explain.tsx"))
    assert "<Disclosure" in code and "defaultOpen" not in code      # closed until asked for
    assert "@/lib/metrics.mjs" in code                               # the static registry
    assert not re.search(r"\b(get|post|fetch)\s*[<(]", code), "Explain must not call the API"
    assert "/ai/" not in code


def test_the_flag_threshold_in_explain_is_the_apis_default() -> None:
    from rrip.api.ai_routes import anomalies

    default = inspect.signature(anomalies).parameters["z_threshold"].default
    assert f"at least {default} " in METRICS["flagged_weeks"]["body"]


def test_the_forecast_explanation_matches_what_is_deployed() -> None:
    from rrip.forecast.contract import HORIZON_WEEKS

    deployed = json.loads((ROOT / "models/forecast/metadata.json").read_text())["model_type"]
    body = METRICS["forecast"]["body"]
    assert deployed == "trailing_mean_4" and "last four completed weeks" in body
    assert HORIZON_WEEKS == 1 and "One week ahead only" in body


# --- no misleading chart ---------------------------------------------------------

def test_the_enrolment_floor_is_the_forecasting_contracts() -> None:
    from rrip.forecast.contract import HISTORY_FLOOR_WEEK

    m = re.search(r"export const ENROLMENT_FLOOR_WEEK = (\d+);", _read("lib/overview.mjs"))
    assert m and int(m.group(1)) == HISTORY_FLOOR_WEEK
    assert f"Weeks before {HISTORY_FLOOR_WEEK} reflect households still joining" \
        in METRICS["weekly_revenue"]["body"]


def test_the_weekly_chart_marks_the_enrolment_period() -> None:
    chart = _code_only(_read("components/overview.tsx"))
    assert "<ReferenceArea" in chart
    assert "households still joining the panel, not demand" in chart
    page = _code_only(_read("app/page.tsx"))
    assert "floor={ENROLMENT_FLOOR_WEEK}" in page
    assert "should not be read as growth" in page


@pytest.mark.parametrize("path", UI_SOURCES, ids=lambda p: p.relative_to(FRONTEND).as_posix())
def test_nothing_is_labelled_as_growth(path: Path) -> None:
    code = _code_only(path.read_text(encoding="utf-8"))
    assert not re.search(r"(?i)\b(revenue|sales|demand|household)s?\s+growth\b|\bgrowth\s+"
                         r"(rate|trend|curve)\b|\bgrew\b", code)


# --- navigation: one product -----------------------------------------------------

def _internal_links() -> set[str]:
    links = set(re.findall(r"href: '(/[^']*)'", _read("lib/overview.mjs")))
    for path in UI_SOURCES:
        links |= set(re.findall(r"""href=["'](/[^"']*)["']""", path.read_text(encoding="utf-8")))
    return links


def test_every_internal_link_points_at_a_page_that_exists() -> None:
    links = _internal_links()
    assert {"/query", "/forecast", "/causal", "/#what-changed"} <= links
    for link in links:
        route = link.split("#")[0].split("?")[0] or "/"
        assert route in PAGES, f"{link} points at no page"
        assert (FRONTEND / PAGES[route]).is_file()
    assert 'id="what-changed"' in _read("app/page.tsx")


@pytest.mark.parametrize("route", sorted(PAGES))
def test_every_page_carries_the_workflow_and_one_page_header(route: str) -> None:
    code = _code_only(_read(PAGES[route]))
    assert code.count("<Workflow") == 1
    assert code.count("<PageHeader") == 1 and "<h1" not in code


def test_the_overview_hands_off_to_ask_with_verified_questions() -> None:
    page = _code_only(_read("app/page.tsx"))
    examples = _read("lib/examples.ts")
    for benchmark in re.findall(r"verified\('([\w-]+)'\)", page):
        assert f'benchmark: "{benchmark}"' in examples, f"{benchmark} is not a verified example"
    assert set(re.findall(r"verified\('([\w-]+)'\)", page)) == {"pub-02", "pub-07"}
    assert "/forecast?department=" in page and 'href="/causal"' in page


# --- design consistency and accessibility ----------------------------------------

@pytest.mark.parametrize("path", UI_SOURCES, ids=lambda p: p.relative_to(FRONTEND).as_posix())
def test_no_raw_palette_classes_or_hex_colours(path: Path) -> None:
    code = _code_only(path.read_text(encoding="utf-8"))
    raw = re.findall(r"\b(?:slate|gray|zinc|stone|emerald|amber|red|blue|green|yellow)-\d{2,3}\b"
                     r"|\b(?:bg|text|border)-(?:white|black)\b|#[0-9a-fA-F]{6}\b", code)
    assert not raw, f"raw colours instead of tokens: {sorted(set(raw))}"


@pytest.mark.parametrize("path", [p for p in UI_SOURCES if "<ResponsiveContainer" in p.read_text(
    encoding="utf-8")], ids=lambda p: p.relative_to(FRONTEND).as_posix())
def test_every_chart_has_a_text_alternative(path: Path) -> None:
    code = _code_only(path.read_text(encoding="utf-8"))
    charts = code.count("<ResponsiveContainer")
    labelled = len(re.findall(r"<figure\b[^>]*?aria-label=", code, re.S))
    assert labelled >= charts, f"{charts} charts, {labelled} labelled figures"


def test_wide_content_wraps_or_scrolls_instead_of_overflowing() -> None:
    assert "break-words" in _read("components/ui.tsx")            # KPI and range values
    assert "overflow-auto" in _read("components/overview.tsx")    # the weekly table
    assert "grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-5" in _read("app/page.tsx")
