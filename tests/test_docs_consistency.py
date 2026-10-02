"""The README, the evaluation report and the deployment doc must tell one story.

They did not. The README quoted a published-tier benchmark the evaluation
report listed as "not measured"; the deployment doc said the hosted tier was
about 50,000 rows and 12 MB while the README's predecessor said 120,800 rows
and 14.6 MB; and two Diebold-Mariano p-values were quoted as if they compared
the deployed baseline with other baselines, when every recorded test is the
model against a baseline.

Each figure here is derived from its recorded source, then every document that
quotes it must quote that value and no other.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from rrip.eval.report import wilson

ROOT = Path(__file__).resolve().parents[1]
DOCS = ("README.md", "reports/eval/latest.md", "docs/deployment.md")


def _text(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _flat(rel: str) -> str:
    return re.sub(r"\s+", " ", _text(rel))


def _json(rel: str) -> dict:
    return json.loads(_text(rel))


PUBLISHED = _json("reports/eval/latest-published.json")
TIER = _json("reports/eval/published-tier.json")
FORECAST = _json("models/forecast/metadata.json")

LOCAL = (29, 31)                 # the recorded local run, as reports/eval/latest.md prints it
_graded = [c for c in PUBLISHED["cases"] if c["expectation"] == "correct"]
HOSTED = (sum(c["passed"] for c in _graded), len(_graded))


def _interval(k: int, n: int) -> tuple[str, str]:
    lo, hi = wilson(k, n)
    return f"{100 * lo:.1f}", f"{100 * hi:.1f}"


INTERVALS = {_interval(*LOCAL), _interval(*HOSTED)}
ROWS, SIZE = f"{TIER['rows']:,}", f"{TIER['megabytes']} MB"


# --- headline numbers ------------------------------------------------------------

def test_the_recorded_sources_give_the_headline_numbers() -> None:
    assert HOSTED == (6, 8)
    assert _interval(*LOCAL) == ("79.3", "98.2") and _interval(*HOSTED) == ("40.9", "92.9")
    assert (TIER["tables"], ROWS, SIZE) == (23, "120,800", "13.3 MB")
    assert sum(t["rows"] for t in TIER["per_table"]) == TIER["rows"]
    assert sum(t["bytes"] for t in TIER["per_table"]) == TIER["bytes"]
    assert round(TIER["bytes"] / 1024 / 1024, 1) == TIER["megabytes"]


@pytest.mark.parametrize("doc", DOCS)
def test_every_document_states_the_same_headline_numbers(doc: str) -> None:
    text = _flat(doc)
    for figure in (f"{LOCAL[0]}/{LOCAL[1]}", f"{100 * LOCAL[0] / LOCAL[1]:.1f}%",
                   f"{HOSTED[0]}/{HOSTED[1]}", ROWS, SIZE):
        assert figure in text, f"{doc} does not state {figure}"

    # No other score out of 31 or out of 8, and no other Wilson interval.
    assert set(re.findall(r"\b(\d+)/31\b", text)) == {str(LOCAL[0])}, doc
    assert set(re.findall(r"(?<![\d/])(\d)/8\b", text)) == {str(HOSTED[0])}, doc
    quoted = set(re.findall(r"Wilson (?:CI|interval) (\d\d\.\d)%?[–-](\d\d\.\d)%", text))
    assert quoted == INTERVALS, f"{doc} quotes Wilson intervals {quoted}"

    # Wherever a size follows the row count, it is the measured one.
    assert set(re.findall(rf"{ROWS} rows\D{{1,8}}(\d+(?:\.\d+)?) MB", text)) <= {SIZE[:-3]}, doc


@pytest.mark.parametrize("doc", (*DOCS, "docs/engineering-notes.md", "src/rrip/publish/tables.py"))
def test_the_superseded_published_tier_figures_are_gone(doc: str) -> None:
    text = _flat(doc)
    for stale in ("14.6 MB", "50,000 rows", "~50k rows", "about 12 MB", "roughly 12 MB"):
        assert stale not in text, f"{doc} still says {stale!r}"


def test_the_evaluation_report_no_longer_calls_the_published_tier_unmeasured() -> None:
    report = _text("reports/eval/latest.md")
    assert "## NL to SQL, published tier" in report
    assert not re.search(r"(?i)not measured", report)
    for case in ("pub-04", "pub-09"):
        assert f"| `{case}` |" in report


def test_the_deployment_doc_lists_every_published_table_with_its_measured_rows() -> None:
    listed = dict(re.findall(r"\| `(pub_\w+)` \| ([\d,]+) \|", _text("docs/deployment.md")))
    measured = {t["table"]: f"{t['rows']:,}" for t in TIER["per_table"]}
    assert listed == measured
    assert f"{TIER['tables']} tables, {ROWS} rows and {SIZE}" in _flat("docs/deployment.md")


# --- Diebold-Mariano: which test each p-value belongs to --------------------------

DM = FORECAST["metrics"]["diebold_mariano_pairwise"]
DEPLOYED = FORECAST["model_type"]


def _label(key: str) -> str:
    return key.replace("_", " ")


def test_every_recorded_dm_test_is_the_model_against_a_baseline() -> None:
    assert DM and all(key.startswith("model_vs_") for key in DM)
    decision = FORECAST["deployment"]["diebold_mariano"]["p_value"]
    assert decision == DM[f"model_vs_{DEPLOYED}"]["p_value"] == 0.99227
    assert FORECAST["deployment"]["baseline"] == DEPLOYED
    assert DM["model_vs_trailing_mean_8"]["p_value"] == 0.25873
    assert DM["model_vs_trailing_median_8"]["p_value"] == 0.16945


def test_the_forecast_report_attributes_each_p_value_to_its_test() -> None:
    report = _flat("reports/eval/forecast_release.md")
    for key, test in DM.items():
        assert f"| {_label(key)} | {test['mean_loss_diff']} | {test['p_value']} |" in report
    for key in (f"model_vs_{DEPLOYED}", "model_vs_trailing_mean_8", "model_vs_trailing_median_8"):
        assert f"{_label(key)}, p = {DM[key]['p_value']}" in report, key
    assert f"| Diebold-Mariano p ({_label('model_vs_' + DEPLOYED)}) | 0.99227 |" in report
    # The old wording read as the deployed baseline against the other two.
    assert "gives p = 0.25873 against" not in report
    assert "none compares one baseline with another" in report


def test_the_page_and_the_docs_label_the_same_tests_the_same_way() -> None:
    page = _text("frontend/app/forecast/page.tsx")
    assert "diebold_mariano_pairwise" in page
    assert "const dmLabel = (key: string) => key.replace(/_/g, ' ');" in page
    assert "key === `model_vs_${summary.model_type}`" in page
    assert "No row tests one baseline against another." in _flat("frontend/app/forecast/page.tsx")

    readme = _flat("README.md")
    assert "Diebold-Mariano p-value, model vs four-week trailing mean | 0.99 |" in readme

    notes = _flat("docs/engineering-notes.md")
    assert "model vs trailing mean 4, p = 0.99" in notes
    assert "model vs trailing mean 8, p = 0.26" in notes
    assert "model vs trailing median 8, p = 0.17" in notes
    assert "p = 0.26 against the 8-week mean" not in notes
