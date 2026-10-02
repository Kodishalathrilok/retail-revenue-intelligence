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
FORECAST_METRICS = ("forecast", "forecast_range", "forecast_error", "forecast_confidence",
                    "forecast_method", "forecast_challenger")
CAUSAL_METRICS = ("campaign_effect", "campaign_interval", "campaign_stderr", "pretrend_test")


# --- Explain: static, and true to the backend ------------------------------------

def test_the_parser_sees_every_explain_entry() -> None:
    assert {"revenue", "baskets", "households", "avg_basket", "units", "weekly_revenue",
            "flagged_weeks", "rfm_segments", *FORECAST_METRICS, *CAUSAL_METRICS} <= set(METRICS)


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


def test_the_method_comparison_matches_the_recorded_deployment_decision() -> None:
    meta = json.loads((ROOT / "models/forecast/metadata.json").read_text())
    dep, body = meta["deployment"], METRICS["forecast_method"]["body"]
    assert dep["deployed_kind"] == "baseline" and "so the trailing mean runs" in body
    assert not dep["model_wape_lower"] and not dep["difference_significant"]   # "Neither held"
    assert f"p < {dep['alpha']}" in body and "Diebold-Mariano" in dep["rule"]
    assert "selected on validation" in body and "selected on validation" in dep["rule"]
    # "Some other simple methods scored lower on the test weeks" is a stated
    # limit, so it has to be true of the recorded scores.
    scores = meta["metrics"]["test_baselines"]
    in_use = scores[meta["model_type"]]["wape"]
    assert any(v["wape"] < in_use for k, v in scores.items() if not k.startswith("__"))


def test_the_challenger_explanation_matches_the_recorded_comparison() -> None:
    # The entry quotes the recorded result, so it is pinned to the metadata:
    # the page must never say more for the challenger than the test run did.
    from rrip.forecast import train

    meta = json.loads((ROOT / "models/forecast/metadata.json").read_text())
    dep, body = meta["deployment"], METRICS["forecast_challenger"]["body"]
    assert dep["deployed_kind"] == "baseline" and "tested, and not deployed" in body
    assert dep["baseline"] == meta["model_type"] == "trailing_mean_4"
    assert f"the model’s error was {dep['model_scores']['wape']:.2f}%" in body
    assert f"four-week average’s was {dep['baseline_scores']['wape']:.2f}%" in body
    assert (dep["model_scores"]["wape"], dep["baseline_scores"]["wape"]) == (9.8564, 9.852)
    pairwise = meta["metrics"]["diebold_mariano_pairwise"]["model_vs_trailing_mean_4"]
    assert dep["diebold_mariano"]["p_value"] == pairwise["p_value"] == 0.99227
    assert ("Diebold-Mariano test of the model against the four-week trailing mean could "
            f"not tell them apart (p = {pairwise['p_value']})") in body
    assert meta["challenger"]["name"].startswith("hgb(") and "gradient-boosting model" in body
    # "fitted on the training weeks" and "a ratio ... converted back to dollars".
    assert any("trained on TRAIN weeks only" in note for note in meta["notes"])
    source = inspect.getsource(train.run)
    assert "MD.to_dollars(model.predict(F.feature_matrix(f))" in source
    assert "fitted on the training weeks" in body and "converted back to dollars" in body
    # The served interval is the live predictor's, as the entry says.
    assert meta["conformal"]["calibrated_for"] == meta["model_type"]
    assert "belongs to the live forecast, not to the challenger" in body


def test_the_confidence_label_thresholds_are_the_services() -> None:
    from rrip.forecast import service

    body = METRICS["forecast_confidence"]["body"]
    assert f"Up to {service.LIMITED_WAPE_MULTIPLE:g} times is NORMAL" in body
    assert f"up to {service.LOW_WAPE_MULTIPLE:g} times is LIMITED" in body


def test_the_forecast_range_explanation_matches_the_calibration() -> None:
    from rrip.forecast.contract import VALIDATION_WEEKS

    meta = json.loads((ROOT / "models/forecast/metadata.json").read_text())
    calibration = meta["conformal"]["calibration"]["0.80"]
    body = METRICS["forecast_range"]["body"]
    assert "split conformal" in calibration["method"] and "Split conformal" in body
    assert "signed" in calibration["method"] and "The two sides can differ" in body
    assert tuple(calibration["calibration_weeks"]) == VALIDATION_WEEKS
    assert "validation weeks" in body
    assert calibration["level"] == 0.8 and "80% level" in body


def test_the_causal_explanations_match_the_estimator() -> None:
    pd = pytest.importorskip("pandas")
    from rrip.ai import causal

    # Sixty households, four weeks each, with a per-household level so the
    # residuals are real. Only the interval's construction is under test.
    rows = [{"household_key": hh, "treated": hh % 2, "post": week // 2,
             "spend": float(40 + (hh * 7 % 11) + 3 * (week // 2) * (hh % 2) + (hh + week) % 5)}
            for hh in range(60) for week in range(4)]
    res = causal.estimate_did(pd.DataFrame(rows))
    half_width = (res["ci_high"] - res["ci_low"]) / 2
    assert half_width / res["did_stderr"] == pytest.approx(1.96, abs=0.001)
    assert "plus and minus 1.96 standard errors" in METRICS["campaign_interval"]["body"]

    source = inspect.getsource(causal.estimate_did)
    assert 'cov_type="cluster"' in source and '"groups": df["household_key"]' in source
    assert "clustered by household" in METRICS["campaign_stderr"]["body"]

    alpha = inspect.signature(causal.check_parallel_trends).parameters["alpha"].default
    assert f"p-value below {alpha} " in METRICS["pretrend_test"]["body"]


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


# --- Forecast and Causal: business-readable, and no overclaiming -------------------

def _explained(route: str) -> list[str]:
    groups = re.findall(r"<Explain metrics=\{\[([^\]]+)\]\}", _code_only(_read(PAGES[route])))
    return [k for g in groups for k in re.findall(r"'(\w+)'", g)]


def test_the_forecast_page_explains_every_forecast_figure() -> None:
    assert _explained("/forecast") == list(FORECAST_METRICS)


def test_the_causal_page_explains_effect_interval_error_and_pretrend() -> None:
    assert _explained("/causal") == list(CAUSAL_METRICS)


def test_the_forecast_page_leads_with_the_number_and_folds_the_technical_record() -> None:
    page = _code_only(_read("app/forecast/page.tsx"))
    order = [page.index(marker) for marker in (
        "What to expect in week", 'title="History and forecast"',
        'title="How this forecast is made"', '<Disclosure summary="Technical detail')]
    assert order == sorted(order)
    assert "one week ahead" in page
    technical = page[order[-1]:]
    for detail in ("Every method on the held-out test weeks", "deployment_rationale",
                   "Data windows"):
        assert detail in technical, f"{detail} should sit behind the technical disclosure"


def test_the_forecast_banner_sits_under_the_cards_in_plain_words() -> None:
    page = _code_only(_read("app/forecast/page.tsx"))
    line = "A simple 4-week average matched the ML model's accuracy, so we deploy the simpler one."
    assert page.count(line) == 1
    opening = '<Callout tone="caution" title="The machine-learning model did not earn deployment.">'
    cards, banner, chart, technical = (page.index(marker) for marker in (
        'label="Typical error, this department"', opening,
        'title="History and forecast"', '<Disclosure summary="Technical detail'))
    assert cards < banner < chart < technical, "the banner belongs under the four cards"
    body = page[banner + len(opening):page.index("</Callout>", banner)]
    assert "{BANNER_LINE}" in body
    # The fixed line plus at most one more sentence.
    assert re.sub(r"\{[^{}]*\}", "", body).count(".") == 1

    # Plain names in the main view; the predictors' own names, the stored
    # rationale and the test labels only inside Technical detail.
    main, detail = page[page.index("return ("):technical], page[technical:]
    for raw in ("summary.deployment_rationale", "summary.challenger.name", "{b.name",
                "model_version", "dmLabel("):
        assert raw not in main, f"{raw} is rendered outside Technical detail"
        assert raw in detail, f"{raw} is no longer in Technical detail"
    assert "'4-week average'" in page and "Machine-learning model (tested)" in main
    assert "one week ahead" in main


def test_the_challenger_is_shown_beside_the_live_forecast_and_labelled_not_deployed() -> None:
    page = _code_only(_read("app/forecast/page.tsx"))
    assert "const CHALLENGER_LABEL = 'Challenger (not deployed)';" in page
    note = ("Both methods were equally accurate on held-out weeks, so the simpler one is live. "
            "The challenger is shown for comparison only.")
    assert f"const CHALLENGER_NOTE = '{note}';" in page

    # The figure shown is the payload's own challenger value: the page hands the
    # forecast to challengerComparison, which passes that value through
    # (frontend/lib/overview.test.mjs), and prints it with the shared formatter.
    assert "const versus = forecast ? challengerComparison(forecast) : null;" in page
    lib = _code_only(_read("lib/overview.mjs"))
    assert "const challenger = Number(c.prediction);" in lib
    assert "const c = forecast?.challenger_model;" in lib and "c.deployed" in lib
    block = page[page.index("{versus && summary && baselineRuns && ("):]
    block = block[:block.index("</section>")]
    for shown in ("{fmtMoney(versus.live)}", "{fmtMoney(versus.challenger)}",
                  "{versus.differenceText}", "{CHALLENGER_LABEL}", "{CHALLENGER_NOTE}",
                  "{methodName(summary.model_type)}", "Machine-learning model"):
        assert shown in block, f"the comparison lost {shown}"
    # A table with a caption is the text alternative: there is no chart here.
    assert "<table" in block and "<caption" in block and "<ResponsiveContainer" not in block
    assert block.count('scope="row"') == 3 and block.count('scope="col"') == 2

    # Under the banner, above the chart, and in plain names.
    banner, comparison, chart, technical = (page.index(marker) for marker in (
        "{BANNER_LINE}", "{versus && summary && baselineRuns && (",
        'title="History and forecast"', '<Disclosure summary="Technical detail'))
    assert banner < comparison < chart < technical
    assert "challenger_model.name" not in page, "the raw name belongs in Technical detail"
    assert not re.search(r"hgb|trailing_mean", block)


COMPARATIVE = re.compile(
    r"\b(better|improved?|improves|improvement|outperform\w*|superior|beats?|more accurate)\b",
    re.I)
NEGATED = re.compile(r"\b(not|no|never|cannot|neither|nor)\b|n[’']t", re.I)


@pytest.mark.parametrize("path", sorted(p for d in ("app", "components", "lib") for p in (
    FRONTEND / d).rglob("*.*") if p.suffix in {".ts", ".tsx", ".mjs"} and ".test." not in p.name),
    ids=lambda p: p.relative_to(FRONTEND).as_posix())
def test_nothing_says_a_method_is_better_improved_or_outperforms(path: Path) -> None:
    # The recorded result is a tie (9.86% against 9.85%, p = 0.99227). A
    # comparative word may appear only in a sentence that negates it, such as
    # "did not beat" or "was not more accurate".
    code = _code_only(path.read_text(encoding="utf-8"))
    for sentence in re.split(r"(?<=[.!?])\s+|\n", code):
        if COMPARATIVE.search(sentence):
            assert NEGATED.search(sentence), f"{path.name}: {sentence.strip()[:160]}"


def test_the_stored_pretrend_verdict_and_raw_warnings_are_never_rendered() -> None:
    # The API stores "did not reject parallel trends"; the approved sentence
    # names the opposite null. The pages build their own sentence and never
    # print the stored one, and stored warnings pass through displayWarning.
    for path in UI_SOURCES + sorted((FRONTEND / "lib").glob("*.mjs")):
        if ".test." in path.name:
            continue
        code = _code_only(path.read_text(encoding="utf-8"))
        assert not re.search(r"parallel_trends\.verdict|\bpt\.verdict\b", code), path.name
        assert "did not reject parallel trends" not in code, path.name
    causal = _code_only(_read("app/causal/page.tsx"))
    assert "{displayWarning(w)}" in causal and ">{w}<" not in causal
    assert "campaign_effect: a.warnings.map(displayWarning)" in causal
    assert "pretrend_test:" not in causal
    overview = _code_only(_read("app/page.tsx"))
    assert "pretrendSentence(campaign.data.parallel_trends)" in overview
    assert "campaign.data.warnings.map(displayWarning)" in overview


def test_the_causal_page_leads_with_effect_interval_and_verdict() -> None:
    page = _code_only(_read("app/causal/page.tsx"))
    page = page[page.index("function AnalysisPanel"):]
    order = [page.index(marker) for marker in (
        'label="Estimated effect on weekly spend"', 'label="95% confidence interval"',
        "title={verdict.headline}", "<Assumptions a={a} />", ">Pre-trend test<")]
    assert order == sorted(order)
    whole = _code_only(_read("app/causal/page.tsx"))
    assert "campaignVerdict(a)" in whole and "pretrendSentence(a.parallel_trends)" in whole
    for term in ("Parallel trends", "No other campaign", "Targeted, not randomised",
                 "What the interval covers"):
        assert f'term="{term}"' in whole


OVERCLAIMS = (
    r"parallel[- ]trends?\s+(hold|holds|held|is proven|are proven|proven|confirmed)",
    r"trends\s+(hold|are parallel)\b",
    r"\b(four|4)[- ]weeks?[- ]ahead\b|\bnext (four|4) weeks\b|\b(four|4)-week forecasts?\b",
    r"\b(there (is|was)|shows?|showed|found|means|had) no effect\b",
)


@pytest.mark.parametrize("path", sorted(p for d in ("app", "components", "lib") for p in (
    FRONTEND / d).rglob("*.*") if p.suffix in {".ts", ".tsx", ".mjs"} and ".test." not in p.name),
    ids=lambda p: p.relative_to(FRONTEND).as_posix())
def test_no_page_overclaims_trends_horizon_or_absence_of_effect(path: Path) -> None:
    code = _code_only(path.read_text(encoding="utf-8"))
    # "does not show there was no effect" is the caveat itself, not the claim.
    code = re.sub(r"(?i)(does not|do not|not the same as) show(ing)?\s+there was no effect", "",
                  code)
    for pattern in OVERCLAIMS:
        assert not re.search(pattern, code, re.I), f"{pattern!r} in {path.name}"


@pytest.mark.parametrize("route,step,verb", [
    ("/query", "explain", "Explain"), ("/forecast", "predict", "Predict"),
    ("/causal", "investigate", "Investigate")])
def test_each_engine_page_marks_its_own_step_in_the_workflow(route: str, step: str,
                                                             verb: str) -> None:
    page = _code_only(_read(PAGES[route]))
    assert f'<Workflow current="{step}" heading="Where next" />' in page
    assert f'eyebrow="{verb} · ' in page
    # The strip closes the page: nothing but closing tags may follow it.
    tail = page[page.index("<Workflow"):].split("/>", 1)[1]
    assert not re.search(r"<[A-Za-z]", tail), "content after the workflow strip"
    steps = dict(re.findall(r"key: '(\w+)', verb: '(\w+)'", _read("lib/overview.mjs")))
    assert steps[step] == verb


def test_the_overview_names_the_same_four_steps() -> None:
    verbs = re.findall(r"verb: '(\w+)'", _read("lib/overview.mjs"))
    assert f'eyebrow="{" → ".join(verbs)}"' in _code_only(_read("app/page.tsx"))


@pytest.mark.parametrize("route", ["/forecast", "/causal"])
def test_every_chart_on_the_engine_pages_has_its_figures_as_a_table(route: str) -> None:
    code = _code_only(_read(PAGES[route]))
    charts = code.count("<ResponsiveContainer")
    assert charts >= 1 and len(re.findall(r"<caption\b", code)) >= charts
    assert "The same figures are in the table below." in code


# --- design consistency and accessibility ----------------------------------------

@pytest.mark.parametrize("path", sorted(p for d in ("app", "components", "lib") for p in (
    FRONTEND / d).rglob("*.*") if p.suffix in {".ts", ".tsx", ".mjs"} and ".test." not in p.name),
    ids=lambda p: p.relative_to(FRONTEND).as_posix())
def test_numbers_are_formatted_in_one_locale(path: Path) -> None:
    # Formatting in the visitor's locale showed the busiest week as "$1,13,193"
    # in its card and "$113,193" in the chart's text. Every formatter names en-US.
    code = _code_only(path.read_text(encoding="utf-8"))
    for call in re.findall(r"(?:toLocaleString|NumberFormat)\(([^,)]*)", code):
        assert call.strip() == "'en-US'", f"locale-dependent formatting in {path.name}"


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
