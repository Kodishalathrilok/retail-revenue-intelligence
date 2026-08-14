"""Regressions from the first live narration benchmark run (2026-08-13).

Six failures came out of that run. FIVE were defects in the measuring apparatus
rather than in the model, which is the more dangerous kind: a benchmark that
misgrades produces confident numbers that are wrong, and nothing about the
output looks suspicious.

Each test below pins one of them using the model's ACTUAL words from the run.
The texts are verbatim, not paraphrased, so a future change to the classifier or
the guard is checked against what a real model really wrote.
"""

from __future__ import annotations

from decimal import Decimal

from rrip.ai.narration import collect_allowed, validate_grounding
from rrip.eval.narration_bench import (
    FAITHFUL,
    SEMANTICALLY_WRONG,
    _asserted_terms,
    _direction_conflict,
    _prose,
    _top_conflict,
    classify,
    truth_set,
)
from rrip.eval.narration_cases import CASES


def case_by_id(cid: str):
    return next(c for c in CASES if c.id == cid)


def ok(text: str):
    from rrip.ai.narration import NarrationResult

    return NarrationResult(ok=True,
                           narrative={"headline": text, "insights": [],
                                      "caveats": []},
                           provider="test")


# --- guard defect: sign blindness -----------------------------------------

def test_a_decrease_of_nineteen_percent_is_grounded_by_negative_nineteen():
    """REGRESSION (guard). NAR-061: percent_change was -19.0 and the model wrote
    "a decrease of 19.0 percent" -- correct English, rejected by the guard.

    The guard read the minus sign as part of the number rather than as the
    direction the sentence already carried in words.
    """
    assert 19.0 in collect_allowed({"percent_change": -19.0})
    data = {"change": {"percent_change": -19.0, "from_value": 9000.0,
                       "to_value": 7290.0}}
    assert validate_grounding(
        "Revenue moved from 9000.0 to 7290.0, a decrease of 19.0 percent.",
        data) == []


def test_sign_tolerance_does_not_admit_an_unrelated_number():
    """The loosening must be exactly one magnitude, not a free pass."""
    data = {"percent_change": -19.0}
    violations = validate_grounding("Revenue fell 19.0%, then 23.0%.", data)
    assert [v.value for v in violations] == [23.0]


def test_positive_values_do_not_admit_their_negation():
    """Only |negative| is added. A positive must not ground its own negative."""
    assert -19.0 not in collect_allowed({"percent_change": 19.0})


# --- classifier defect: field joining -------------------------------------

def test_fields_are_punctuated_so_sentences_do_not_merge():
    """REGRESSION (classifier). NAR-150: an unpunctuated so_what ran into the
    next statement, creating "…the peak performing week in the dataset Week 9
    dropped to…" -- one apparent sentence with a superlative and the wrong week.
    """
    narrative = {
        "headline": "Week 8 achieved the highest revenue",
        "insights": [
            {"statement": "Week 8 generated 91000.00",
             "so_what": "identifies the peak performing week in the dataset"},
            {"statement": "Week 9 dropped to 64000.00", "so_what": "the weakest"},
        ],
        "caveats": [],
    }
    text = _prose(narrative)
    assert "dataset. Week 9" in text, "field boundary must end a sentence"
    assert _top_conflict(text, case_by_id("NAR-150")) is None


def test_the_actual_nar150_narration_grades_faithful():
    """Verbatim from the run. The narration was right; the grading was wrong."""
    text = _prose({
        "headline": "Week 8 achieved the highest revenue while week 9 recorded "
                    "the lowest revenue",
        "insights": [
            {"statement": "Week 8 generated a revenue of 91000.00, making it "
                          "the strongest performing period",
             "so_what": "identifies the peak performing week in the dataset"},
            {"statement": "Week 9 dropped to a revenue of 64000.00, "
                          "representing the weakest period",
             "so_what": "highlights the lowest point of revenue capture"},
        ],
        "caveats": ["Analysis is limited to the three weeks provided"],
    })
    case = case_by_id("NAR-150")
    verdict, reasons, _ = classify(case, ok(text), truth_set(case))
    assert verdict == FAITHFUL, reasons


# --- classifier defect: negation blindness --------------------------------

def test_declining_to_state_profitability_is_not_asserting_it():
    """REGRESSION (classifier). NAR-160, verbatim: the model explicitly refused
    to infer profit from revenue and was graded as having claimed it."""
    text = ("The available data provides revenue figures for the grocery "
            "department without detailing costs or profitability. The GROCERY "
            "department generated a revenue of 4100000.00. While this shows the "
            "top-line sales volume, it is not possible to determine profit "
            "margins or net income from revenue alone. Profitability cannot be "
            "assessed because the dataset only contains revenue and lacks cost "
            "data.")
    case = case_by_id("NAR-160")
    verdict, reasons, _ = classify(case, ok(text), truth_set(case))
    assert verdict == FAITHFUL, reasons


def test_saying_the_data_contains_no_forecast_is_not_forecasting():
    """REGRESSION (classifier). NAR-162, verbatim."""
    text = ("Revenue was 84000.00 in week 50 and increased by 2000.0 to reach "
            "86000.00 in week 51. The data provided covers only two periods and "
            "does not contain a forecast for future weeks.")
    case = case_by_id("NAR-162")
    verdict, reasons, _ = classify(case, ok(text), truth_set(case))
    assert verdict == FAITHFUL, reasons


def test_growth_inside_a_disclaimer_is_not_a_claim_of_growth():
    """REGRESSION (classifier). NAR-062: "campaign growth cannot be fully
    evaluated" was read as asserting an increase on falling data."""
    assert _direction_conflict(
        "The provided data only includes two weeks, so campaign growth cannot "
        "be fully evaluated without additional period context.",
        "decrease") is None


def test_an_unnegated_forbidden_term_is_still_caught():
    """The negation fix must not disable the check."""
    assert _asserted_terms("This is a highly profitable department.",
                           ("profitable",)) == ["profitable"]
    assert _asserted_terms("Profitability cannot be assessed here.",
                           ("profitab",)) == []


def test_an_unnegated_direction_error_is_still_caught():
    assert _direction_conflict("Revenue rose sharply this week.",
                               "decrease") is not None


# --- the one real model failure -------------------------------------------

def test_echoing_annualised_framing_remains_a_violation():
    """NAR-174, verbatim, and the only semantic failure that survived review.

    "The annualised position shows a single recorded week of performance."
    States no annual figure, so nothing numeric is wrong -- but it accepts the
    question's framing that an annualised view exists in one week of data. It is
    the weakest call in the set and it is kept rather than dropped, because
    dropping the one inconvenient failure is how a benchmark stops being one.
    """
    text = ("The annualised position shows a single recorded week of "
            "performance. Week 1 recorded a revenue of 1000.00.")
    case = case_by_id("NAR-174")
    verdict, reasons, _ = classify(case, ok(text), truth_set(case))
    assert verdict == SEMANTICALLY_WRONG
    assert "annualised" in " ".join(reasons)


def test_decimal_and_sign_fixes_coexist():
    """Both guard fixes found by benchmarks, together on one Postgres payload."""
    data = {"rows": [{"pct": Decimal("-19.00"), "revenue": Decimal("7290.00")}]}
    assert validate_grounding("Revenue of 7,290.00 fell 19.0%.", data) == []
