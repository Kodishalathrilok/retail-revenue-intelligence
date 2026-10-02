"""Every figure in the README must trace to a tracked file.

The README once quoted a test count that was hundreds of tests out of date and
a framework version the project had left behind, because nothing connected its
numbers to the things they described. This test is that connection:

* each figure the README quotes is derived here from its source -- a recorded
  evaluation run, the model metadata, a constant in the code -- and must come
  out as the same text;
* any other figure that appears in the README's prose fails the scan, so a new
  claim cannot be added without saying where it comes from.

Only "claim-like" tokens are scanned: anything with a decimal point, a
thousands separator or a percent sign, or three or more digits. Small bare
integers (a list of 5 departments, step 2) are asserted individually below
where they are claims.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from rrip.ai import nl2sql
from rrip.config import Settings
from rrip.eval.report import wilson

ROOT = Path(__file__).resolve().parents[1]


def _text(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _json(rel: str) -> dict:
    return json.loads(_text(rel))


def _flat(text: str) -> str:
    """Whitespace collapsed, so a phrase check survives a re-wrapped line."""
    return re.sub(r"\s+", " ", text)


def _stated(rel: str, context: str, figure: str | None = None) -> str:
    """`figure` if the source states `context`, else an empty string (which
    never equals a figure). With no figure given, the context is the figure."""
    return (figure or context) if context in _flat(_text(rel)) else ""


def _pct(x: float, dp: int = 1) -> str:
    # round() before formatting: 100 * 0.8075 is 80.74999..., which the format
    # alone would print as 80.7 while the page and the report say 80.8.
    return f"{round(x, dp + 6):.{dp}f}%"


README = _text("README.md")
FLAT = _flat(README)
PROFILE, DEPLOY = "docs/dataset-profile.md", "docs/deployment.md"
LOCAL_EVAL = "reports/eval/latest.md"
EXAMPLES = "frontend/lib/examples.ts"
PUBLISHED = _json("reports/eval/latest-published.json")
TIER = _json("reports/eval/published-tier.json")
FORECAST = _json("models/forecast/metadata.json")
CAMPAIGN = {c["campaign_id"]: c for c in _json("reports/eval/causal-campaigns.json")["campaigns"]}
C26, C18 = CAMPAIGN[26], CAMPAIGN[18]

PUB_GRADED = [c for c in PUBLISHED["cases"] if c["expectation"] == "correct"]
PUB_PASSED = [c for c in PUB_GRADED if c["passed"]]
LOCAL_CI = wilson(29, 31)
PUB_CI = wilson(len(PUB_PASSED), len(PUB_GRADED))
SCORES = FORECAST["metrics"]["test_baselines"]
PER_DEPT = [d["wape"] for d in FORECAST["metrics"]["test"]["per_department"].values()]
COVERAGE = FORECAST["conformal"]["test_coverage"]
TEST_WEEKS = list(FORECAST["contract"]["windows"]["test"])

# README figure -> the same text, derived from where it comes from.
TRACED: dict[str, str] = {
    # the dataset
    "2,595,732": _stated(PROFILE, "2,595,732"),
    "36,771,279": _stated(PROFILE, "36,771,279"),
    "2,500": _stated(PROFILE, "2,500 panel households", "2,500"),
    "39.6": _stated(DEPLOY, "39.6M-row load", "39.6"),
    "3,713": _stated(DEPLOY, "3,713 MB", "3,713"),
    "120,800": f"{TIER['rows']:,}",
    "13.3": str(TIER["megabytes"]),
    # NL->SQL, local tier
    "93.5%": _stated(LOCAL_EVAL, "29/31 = **93.5%**", _pct(100 * 29 / 31)),
    "79.3%": _pct(100 * LOCAL_CI[0]),
    "98.2%": _pct(100 * LOCAL_CI[1]),
    "100.0%": _stated(LOCAL_EVAL, "| refusal rate | 100.0% | 100.0% |", "100.0%"),
    # NL->SQL, published tier
    "75.0%": _pct(100 * len(PUB_PASSED) / len(PUB_GRADED)),
    "40.9%": _pct(100 * PUB_CI[0]),
    "92.9%": _pct(100 * PUB_CI[1]),
    # forecast
    "101": str(TEST_WEEKS[1]),
    "9.86%": _pct(SCORES["__challenger_model__"]["wape"], 2),
    "9.85%": _pct(SCORES[FORECAST["model_type"]]["wape"], 2),
    "0.99": f"{FORECAST['deployment']['diebold_mariano']['p_value']:.2f}",
    "0.05": str(FORECAST["deployment"]["alpha"]),
    "80%": _pct(100 * COVERAGE["0.80"]["nominal"], 0),
    "80.8%": _pct(100 * COVERAGE["0.80"]["coverage"]),
    "95%": _pct(100 * COVERAGE["0.95"]["nominal"], 0),
    "94.7%": _pct(100 * COVERAGE["0.95"]["coverage"]),
    "31.6%": _pct(FORECAST["metrics"]["macro_wape_test"]),
    "7.0%": _pct(min(PER_DEPT)),
    "117.1%": _pct(max(PER_DEPT)),
    # causal
    "1.96": f"{(C26['ci_high'] - C26['ci_low']) / 2 / C26['did_stderr']:.2f}",
    "5.0": _stated(LOCAL_EVAL, "| true effect | 5.0 |", "5.0"),
    "100": _stated(LOCAL_EVAL, "| simulations | 100 |", "100"),
    "95.0%": _stated(LOCAL_EVAL, "| 95% CI coverage | 95.0% (nominal 95.0%) |", "95.0%"),
    "12.085": _stated(LOCAL_EVAL, "| VIOLATED parallel trends | 5.0 | 12.085 |", "12.085"),
    "310": str(C26["treated_n"]),
    "2,140": f"{C26['control_n']:,}",
    "104": str(C18["treated_n"]),
    "933": str(C18["control_n"]),
    "6.6%": _pct(C26["contaminated_pct"]),
    "90.8%": _pct(C18["contaminated_pct"]),
    "6.53": f"{C26['naive_difference']:.2f}",
    "0.37": f"{C18['naive_difference']:.2f}",
    "1.51": f"{C26['did_estimate']:.2f}",
    "2.35": f"{-C26['ci_low']:.2f}",
    "5.36": f"{C26['ci_high']:.2f}",
    "5.35": f"{-C18['did_estimate']:.2f}",
    "11.91": f"{-C18['ci_low']:.2f}",
    "1.22": f"{C18['ci_high']:.2f}",
    "1.97": f"{C26['did_stderr']:.2f}",
    "3.35": f"{C18['did_stderr']:.2f}",
    "0.44": f"{C26['did_pvalue']:.2f}",
    "0.11": f"{C18['did_pvalue']:.2f}",
    "0.39": f"{C26['parallel_trends']['interaction_pvalue']:.2f}",
    "0.012": f"{C18['parallel_trends']['interaction_pvalue']:.3f}",
    # security
    "5,000,000": f"{int(nl2sql.MAX_COST):,}",
    "5,000": f"{nl2sql.MAX_ROWS:,}",
    "120": _stated(LOCAL_EVAL, "default: 120,000 ms", "120"),
    "300": str(Settings.model_fields["llm_daily_cap"].default),
    # the demo
    "45.6%": _stated(EXAMPLES, "Champions, 45.6%", "45.6%"),
}

# Not claims about the project: the Python version and the capture date.
STRUCTURAL = {"3.12", "2026"}


def _prose(text: str) -> str:
    text = re.sub(r"```.*?```", " ", text, flags=re.S)          # code and the diagram
    text = re.sub(r"`[^`]*`", " ", text)                        # inline code, case ids
    text = re.sub(r"<img[^>]*>", " ", text)
    text = re.sub(r"\]\([^)]*\)", "]", text)                    # link and image targets
    return re.sub(r"https?://\S+", " ", text)


def _figures(text: str) -> set[str]:
    tokens = re.findall(r"\d[\d,]*(?:\.\d+)?%?", _prose(text))
    tokens = (t.rstrip(",") for t in tokens)
    return {t for t in tokens if any(c in t for c in ",.%") or len(t) >= 3}


def test_every_traced_figure_matches_its_source() -> None:
    wrong = {k: v for k, v in TRACED.items() if k != v}
    assert not wrong, f"README figure -> what the source gives: {wrong}"


def test_every_figure_in_the_readme_is_traced() -> None:
    untraced = _figures(README) - set(TRACED) - STRUCTURAL
    assert not untraced, f"figures in README.md with no source in this test: {sorted(untraced)}"


def test_no_traced_figure_has_left_the_readme() -> None:
    stale = set(TRACED) - _figures(README)
    assert not stale, f"traced here but no longer in README.md: {sorted(stale)}"


def test_the_small_counts_are_what_the_sources_say() -> None:
    # Bare integers the scan skips, asserted one by one.
    local = _flat(_text(LOCAL_EVAL))
    assert "29/31" in FLAT and "29/31" in local
    assert "52 questions, 31 with a reference answer" in FLAT
    assert "52 cases" in local and "over the 31 cases" in local
    assert "0 of 31" in FLAT and "**0** of 31" in local
    assert (len(PUBLISHED["cases"]), len(PUB_GRADED), len(PUB_PASSED)) == (10, 8, 6)
    assert "10 questions, 8 with a reference answer" in FLAT and "6/8" in FLAT
    assert len(FORECAST["departments"]) == 23 and "for 23 departments" in FLAT
    assert TEST_WEEKS == [88, 101] and "test weeks 88–101" in FLAT
    ranked = sorted(SCORES, key=lambda k: SCORES[k]["wape"])
    assert (ranked.index(FORECAST["model_type"]) + 1, len(ranked)) == (4, 8)
    assert "ranks 4th of the 8 predictors" in FLAT
    assert "16 of 16 probes pass, 14 of the refusals" in FLAT
    assert "16/16 probes passed. 14 refusals came from PostgreSQL itself" in local
    assert "23 `pub_*`" in FLAT and "23 `pub_*` tables" in _flat(_text(DEPLOY))
    assert nl2sql.STATEMENT_TIMEOUT_MS == 15_000 and "15 s statement timeout" in FLAT
    fields = Settings.model_fields
    assert (fields["ai_rate_limit"].default, fields["ai_rate_window_seconds"].default) == (10, 60)
    assert "10 AI requests per client per 60 s" in FLAT
    vercel = _json("frontend/vercel.json")["functions"]["api/index.py"]
    assert vercel["maxDuration"] == 60 and "60 s function limit" in FLAT


def test_the_recorded_failures_are_named_and_not_advertised() -> None:
    failed = {c["id"] for c in PUB_GRADED if not c["passed"]}
    assert failed == {"pub-04", "pub-09"}
    for case in (*failed, "grp-03", "win-02"):
        assert f"`{case}`" in README, f"{case} is a recorded failure the README must name"
    for case in ("grp-03", "win-02"):
        assert f"`{case}`" in _text(LOCAL_EVAL)
    assert "kept as a recorded failure" in FLAT


def test_the_walkthrough_uses_only_verified_demo_questions() -> None:
    demo = _flat(README[README.index("## Two-minute demo"):README.index("## Evaluation evidence")])
    asked = re.findall(r'\*"([^"]+)"\*', demo)
    verified = re.findall(r'question:\s*"([^"]+)"', _text(EXAMPLES))
    assert len(asked) >= 4
    unverified = set(asked) - set(verified)
    assert not unverified, f"not a verified demo question: {unverified}"
    pub09 = next(c["question"] for c in PUBLISHED["cases"] if c["id"] == "pub-09")
    assert pub09 not in demo and pub09 not in verified


def test_the_readme_keeps_the_projects_wording_rules() -> None:
    prose = _flat(_prose(README))
    assert "one week ahead" in prose.lower()
    assert not re.search(r"(?i)\b(four|4)[- ]weeks?[- ]ahead\b|\bnext (four|4) weeks\b"
                         r"|\b(four|4)-week forecasts?\b", prose)
    assert not re.search(r"(?i)parallel[- ]trends?\s+(hold|holds|held|proven|are proven|"
                         r"is proven|confirmed)", prose)
    assert "The test did not reject differential pre-treatment trends" in prose
    assert "do not rule out a zero or small effect" in prose
    assert "not the same as showing there was no effect" in prose
    assert "static text" in prose and "No model generates them" in prose
    assert "503 LOCAL_ONLY" in README


def test_the_first_screen_has_the_pitch_the_demo_and_the_loop() -> None:
    first = _flat(README[:README.index("## Screenshots")])
    assert "https://retail-revenue-intelligence-flame.vercel.app" in first
    assert "Detect → Explain → Predict → Investigate → Measure" in first
    assert "the language model interprets, and deterministic systems calculate" in first


def test_the_diagram_shows_the_role_and_the_published_boundary() -> None:
    diagram = re.search(r"```mermaid\n(.*?)```", README, re.S)
    assert diagram, "no Mermaid architecture diagram"
    for part in ("rrip_ro", "SELECT only", "Published tier", "Answerability router",
                 "Deterministic engines", "Evidence returned with the answer", "Next.js UI"):
        assert part in diagram.group(1), f"the architecture diagram lost: {part}"


def test_the_screenshots_exist_and_are_tracked() -> None:
    shots = set(re.findall(r"docs/screenshots/[\w.-]+", README))
    assert {"overview", "ask", "forecast", "causal"} <= {Path(s).name.split("-")[0] for s in shots}
    assert any("mobile" in s for s in shots)
    tracked = set(subprocess.run(["git", "ls-files", "docs/screenshots"], cwd=ROOT,
                                 capture_output=True, text=True, check=True).stdout.split())
    assert shots <= tracked, f"shown in the README but not tracked: {shots - tracked}"
