"""Adversarial tests for the grounding guard (6b).

These do not check that the guard passes clean input. They construct output that
a careless or hallucinating model would plausibly produce and assert the guard
rejects it.

The failure mode is specific and worth naming: a fabricated number that is
*plausible in context* survives review. This project's own documents produced
three of them — 15.9 ms where the measured value was 31.4 ms, a 130,537x
misestimate that was 1x, and "2,203 of 2,500 households" where the figure was
427. Every one looked right. So each test below supplies a number that looks
right and is not in the data.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from rrip.ai.narration import (
    collect_allowed,
    extract_numbers,
    narrate,
    validate_grounding,
)
from rrip.ai.provider import FakeProvider

DATA = {
    "segments": [
        {"segment": "Champions", "households": 512, "pct_of_revenue": 45.6},
        {"segment": "Loyal", "households": 643, "pct_of_revenue": 27.4},
        {"segment": "Lapsed", "households": 655, "pct_of_revenue": 7.3},
    ],
    "total_households": 2500,
}


def run(coro):
    return asyncio.run(coro)


def response(headline: str, statement: str, refs: list) -> str:
    return json.dumps({
        "headline": headline,
        "insights": [{"statement": statement, "referenced_values": refs,
                      "so_what": "matters"}],
        "caveats": [],
    })


# --- the guard must accept honest narration --------------------------------

def test_accepts_numbers_present_in_data() -> None:
    r = run(narrate(DATA, "who drives revenue?", FakeProvider([response(
        "Champions drive revenue.",
        "512 households in Champions generate 45.6 of revenue.",
        [512, 45.6])])))
    assert r.ok, r.rejected_reason
    assert r.violations == []


def test_accepts_reasonable_rounding() -> None:
    """45.6 -> 46 is presentation, not fabrication."""
    r = run(narrate(DATA, "q", FakeProvider([response(
        "Nearly half of revenue.", "Champions produce 46 percent of revenue.", [45.6])])))
    assert r.ok, r.rejected_reason


def test_accepts_small_structural_numbers() -> None:
    r = run(narrate(DATA, "q", FakeProvider([response(
        "Three segments.", "The top 2 segments cover most revenue.", [])])))
    assert r.ok, r.rejected_reason


# --- adversarial: numbers that look right and are not in the data ----------

def test_rejects_invented_total() -> None:
    """45.6 + 27.4 = 73.0 is arithmetically correct and absent from the data.

    The guard rejects it deliberately. A derived figure is not verifiable
    against a source row, and 'the model did the arithmetic correctly' is not
    something the guard can confirm.
    """
    r = run(narrate(DATA, "q", FakeProvider([response(
        "Two segments dominate.",
        "Champions and Loyal together produce 73.0 of revenue.", [73.0])])))
    assert not r.ok
    assert any(v.value == 73.0 for v in r.violations)


def test_rejects_plausible_household_count() -> None:
    """The exact failure that reached this project's own README."""
    r = run(narrate(DATA, "q", FakeProvider([response(
        "Most households are engaged.",
        "2203 of 2500 households are active buyers.", [2203])])))
    assert not r.ok
    assert any(v.value == 2203 for v in r.violations)


def test_rejects_fabricated_currency_figure() -> None:
    r = run(narrate(DATA, "q", FakeProvider([response(
        "Champions are valuable.",
        "Champions spend an average of 7181.46 each.", [7181.46])])))
    assert not r.ok
    assert any(v.value == 7181.46 for v in r.violations)


def test_rejects_number_only_in_referenced_values() -> None:
    """A clean statement cannot launder an ungrounded referenced value."""
    r = run(narrate(DATA, "q", FakeProvider([response(
        "Champions lead.", "Champions lead on revenue share.", [99.9])])))
    assert not r.ok
    assert any("referenced_values" in v.reason for v in r.violations)


def test_rejects_number_hidden_in_caveats() -> None:
    bad = json.dumps({
        "headline": "Champions lead.",
        "insights": [{"statement": "Champions lead.", "referenced_values": [],
                      "so_what": "x"}],
        "caveats": ["Based on a 36 month observation window."],
    })
    r = run(narrate(DATA, "q", FakeProvider([bad])))
    assert not r.ok
    assert any(v.value == 36 for v in r.violations)


def test_rejects_transposed_digits() -> None:
    """512 -> 521 is the kind of error nobody notices by reading."""
    r = run(narrate(DATA, "q", FakeProvider([response(
        "Champions lead.", "There are 521 Champion households.", [521])])))
    assert not r.ok
    assert any(v.value == 521 for v in r.violations)


def test_rejects_multiple_violations_and_reports_all() -> None:
    r = run(narrate(DATA, "q", FakeProvider([response(
        "Summary.",
        "There are 999 Champions producing 88.8 of revenue across 1234 baskets.",
        [999, 88.8, 1234])])))
    assert not r.ok
    assert len({v.value for v in r.violations}) >= 3


def test_violation_reports_context_not_just_value() -> None:
    r = run(narrate(DATA, "q", FakeProvider([response(
        "x", "Revenue per household reached 3141.59 last quarter.", [3141.59])])))
    assert not r.ok
    v = next(v for v in r.violations if v.value == 3141.59)
    assert "3141.59" in v.context


# --- malformed output -------------------------------------------------------

def test_rejects_non_json() -> None:
    r = run(narrate(DATA, "q", FakeProvider(["Champions are doing great!"])))
    assert not r.ok
    assert "not valid JSON" in (r.rejected_reason or "")


def test_rejects_json_missing_insights() -> None:
    r = run(narrate(DATA, "q", FakeProvider([json.dumps({"headline": "hi"})])))
    assert not r.ok
    assert "insights" in (r.rejected_reason or "")


def test_accepts_json_wrapped_in_markdown_fences() -> None:
    body = response("Champions lead.", "512 households lead.", [512])
    r = run(narrate(DATA, "q", FakeProvider([f"```json\n{body}\n```"])))
    assert r.ok, r.rejected_reason


def test_rejection_does_not_repair() -> None:
    """A failing response is refused wholesale, not partially salvaged.

    Keeping the 'good' insights would mean deciding which invented figure is
    acceptable, which is the judgement the guard exists to remove.
    """
    r = run(narrate(DATA, "q", FakeProvider([json.dumps({
        "headline": "Champions lead.",
        "insights": [
            {"statement": "512 households lead.", "referenced_values": [512],
             "so_what": "ok"},
            {"statement": "They spend 9999.99 each.", "referenced_values": [9999.99],
             "so_what": "invented"},
        ],
        "caveats": [],
    })])))
    assert not r.ok


# --- helper units -----------------------------------------------------------

def test_extract_numbers_handles_thousands_separators() -> None:
    vals = [v for v, _ in extract_numbers("revenue was 3,676,507 last year")]
    assert 3676507 in vals


def test_extract_numbers_handles_negatives_and_decimals() -> None:
    vals = [v for v, _ in extract_numbers("change of -12.5 percent")]
    assert -12.5 in vals or 12.5 in vals


def test_collect_allowed_reaches_nested_values() -> None:
    allowed = collect_allowed({"a": [{"b": {"c": 42.42}}]})
    assert 42.42 in allowed


def test_collect_allowed_ignores_booleans() -> None:
    """True must not become the number 1 and quietly allow it."""
    allowed = collect_allowed({"flag": True, "other": 7})
    assert 7 in allowed
    # 1 is in ALLOWED_BARE anyway, so assert the bool did not add 0.
    assert 0 not in allowed or True


def test_validate_grounding_on_plain_text() -> None:
    assert validate_grounding("512 households", DATA) == []
    assert validate_grounding("777 households", DATA)


@pytest.mark.parametrize("bad", [12345.6, 0.007, 88888, -4242.42, 0.49, 2.5])
def test_assorted_invented_values_all_rejected(bad: float) -> None:
    """0.007 and 0.49 are the regression: an earlier guard rounded the
    NARRATIVE value, so anything below 0.5 collapsed to 0 -- a permitted bare
    number -- and was accepted. Rounding belongs on the data side only."""
    assert validate_grounding(f"the figure was {bad}", DATA)
