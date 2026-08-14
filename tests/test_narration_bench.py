"""Tests for the narration benchmark's own grading.

A benchmark that misgrades is worse than no benchmark, because its output looks
like evidence. These tests run the classifier against hand-built narration
results whose verdict is known, with no model involved.

The most important one is `test_a_derivable_trap_is_demoted`: without it the
answer key could mark a TRUE statement as a wrong one, and the structured path
would be punished for stating something the deterministic layer computed.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from rrip.ai.narration import GroundingViolation, NarrationResult
from rrip.eval.narration_bench import (
    FAITHFUL,
    GROUNDING_REJECTED_CORRECT,
    GROUNDING_REJECTED_STRICT,
    NUMERICALLY_WRONG,
    SEMANTICALLY_WRONG,
    UNSUPPORTED_NUMBER,
    VALID_REFUSAL,
    _direction_conflict,
    _top_conflict,
    classify,
    effective_traps,
    truth_set,
)
from rrip.eval.narration_cases import CASES, NarrationCase, _c


def case_by_id(cid: str) -> NarrationCase:
    return next(c for c in CASES if c.id == cid)


def ok_result(text: str) -> NarrationResult:
    return NarrationResult(
        ok=True,
        narrative={"headline": text, "insights": [], "caveats": []},
        provider="test")


def rejected_result(values: list[float]) -> NarrationResult:
    return NarrationResult(
        ok=False, narrative={"headline": "x", "insights": []},
        violations=[GroundingViolation(v, "ctx", "reason") for v in values],
        provider="test", rejected_reason="guard")


# --- the safety mechanism -------------------------------------------------

def test_a_derivable_trap_is_demoted():
    """A 'trap' the deterministic layer legitimately computes is not a trap.

    NAR-140 lists 6,280,000 (the department sum) as a trap. derive() computes
    totals, so on the structured path that figure is TRUE. Grading it as wrong
    would penalise a correct statement.
    """
    case = case_by_id("NAR-140")
    truth = truth_set(case)
    assert 6280000.0 in truth
    assert 6280000.0 not in effective_traps(case, truth)


def test_the_canonical_percentage_is_true_and_never_a_trap():
    case = case_by_id("NAR-050")
    truth = truth_set(case)
    assert 25.0 in truth, "derive() must supply the correct percent change"
    assert 25.0 not in effective_traps(case, truth)
    assert 20.0 in effective_traps(case, truth), "the wrong denominator stays a trap"


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_no_case_traps_a_value_the_data_supports(case):
    """Whole-key audit: no effective trap may be in the truth set."""
    truth = truth_set(case)
    for trap in effective_traps(case, truth):
        assert trap not in truth, f"{case.id}: trap {trap} is actually derivable"


# --- verdicts --------------------------------------------------------------

def test_faithful_narration():
    case = case_by_id("NAR-001")
    verdict, _r, _o = classify(case, ok_result("There are 2,500 households."),
                               truth_set(case))
    assert verdict == FAITHFUL


def test_invented_number_is_unsupported():
    case = case_by_id("NAR-001")
    verdict, _r, offending = classify(
        case, ok_result("There are 2,500 households across 47 regions."),
        truth_set(case))
    assert verdict == UNSUPPORTED_NUMBER
    assert 47.0 in offending


def test_a_known_wrong_value_is_numerically_wrong():
    case = case_by_id("NAR-050")
    verdict, _r, offending = classify(
        case, ok_result("Revenue rose 20% from 800.00 to 1,000.00."),
        truth_set(case))
    assert verdict == NUMERICALLY_WRONG
    assert 20.0 in offending


def test_the_correct_percentage_grades_faithful():
    case = case_by_id("NAR-050")
    verdict, _r, _o = classify(
        case, ok_result("Revenue rose 25% from 800.00 to 1,000.00."),
        truth_set(case))
    assert verdict == FAITHFUL


def test_wrong_direction_is_semantically_wrong():
    case = case_by_id("NAR-060")
    verdict, reasons, _o = classify(
        case, ok_result("Revenue rose from 40,000.00 to 30,000.00."),
        truth_set(case))
    assert verdict == SEMANTICALLY_WRONG
    assert "increase" in " ".join(reasons)


def test_forbidden_claim_is_semantically_wrong():
    case = case_by_id("NAR-160")
    verdict, reasons, _o = classify(
        case, ok_result("GROCERY earned 4,100,000.00, a profitable department."),
        truth_set(case))
    assert verdict == SEMANTICALLY_WRONG
    assert "profitable" in " ".join(reasons)


def test_guard_rejection_of_an_untrue_number_is_credited_to_the_guard():
    case = case_by_id("NAR-050")
    verdict, _r, _o = classify(case, rejected_result([20.0]), truth_set(case))
    assert verdict == GROUNDING_REJECTED_CORRECT


def test_guard_rejection_of_a_true_number_is_recorded_as_strict():
    """The measurement the whole A/B turns on.

    On the raw path the guard rejects 25% because it is not literally in the
    rows -- even though it is arithmetically true. That is a cost to the user,
    not a save, and it must not be counted as the guard working.
    """
    case = case_by_id("NAR-050")
    verdict, _r, _o = classify(case, rejected_result([25.0]), truth_set(case))
    assert verdict == GROUNDING_REJECTED_STRICT


def test_declining_an_undefined_percentage_is_a_valid_refusal():
    case = case_by_id("NAR-090")
    verdict, _r, _o = classify(
        case,
        ok_result("Revenue rose by 500.00; a percentage change from zero is "
                  "undefined."),
        truth_set(case))
    assert verdict == VALID_REFUSAL


# --- the conservative detectors -------------------------------------------

def test_direction_detector_tolerates_mixed_narratives():
    """Two measures moving oppositely must not be graded as a contradiction."""
    assert _direction_conflict(
        "Revenue increased while basket counts declined.", "increase") is None
    assert _direction_conflict("Revenue declined sharply.", "increase") is not None


def test_direction_detector_flags_invented_movement_on_flat_data():
    assert _direction_conflict("Revenue grew slightly.", "no_change") is not None
    assert _direction_conflict("Revenue was unchanged.", "no_change") is None


def test_top_detector_only_fires_on_a_superlative_naming_a_loser():
    case = case_by_id("NAR-141")
    assert _top_conflict("BEEF leads with 310,000.00.", case) is None
    assert _top_conflict("SOFT DRINKS is the largest commodity.", case) is not None
    # A mention without a superlative is not a ranking claim.
    assert _top_conflict("SOFT DRINKS earned 250,000.00.", case) is None


def test_top_detector_skips_a_null_winner():
    """NAR-082's largest group has a NULL label; nothing can be asserted."""
    assert _top_conflict("The largest group is 50-74K.", case_by_id("NAR-082")) is None


# --- dataset integrity -----------------------------------------------------

def test_dataset_covers_the_required_categories():
    required = {
        "integer_count", "currency", "decimal", "percentage", "absolute_change",
        "relative_change", "negative_change", "zero_change", "nulls",
        "zero_denominator", "large_values", "small_decimals", "rounding",
        "multi_metric", "ranking", "extremes", "unsupported_metric",
        "tempts_arithmetic", "plausible_false",
    }
    assert required <= {c.category for c in CASES}
    assert len(CASES) >= 50


def test_every_case_materialises_decimals():
    for case in CASES:
        rows, columns = case.materialise()
        assert set(columns) == set(case.columns)
        for r in rows:
            for col in case.decimal_columns:
                if r.get(col) is not None:
                    assert isinstance(r[col], Decimal), f"{case.id}.{col}"


def test_every_case_is_gradeable():
    """A case with no trap, no direction, no top and no forbidden term asserts
    nothing beyond the guard, and would pass on any output at all."""
    for case in CASES:
        truth = truth_set(case)
        has_assertion = bool(
            effective_traps(case, truth) or case.expected_direction
            or case.expected_top or case.forbidden_terms
            or case.expect_no_claim_about)
        assert has_assertion, f"{case.id} asserts nothing"


def test_jsonl_round_trips():
    from rrip.eval.narration_cases import export_jsonl, load_jsonl

    export_jsonl()
    loaded = load_jsonl()
    assert [c.id for c in loaded] == [c.id for c in CASES]
    assert loaded[0].materialise() == CASES[0].materialise()


def test_helper_builds_a_case():
    c = _c(id="X", category="t", question="q", columns=["a"], rows=[{"a": 1}])
    assert c.id == "X" and c.rows[0]["a"] == 1
