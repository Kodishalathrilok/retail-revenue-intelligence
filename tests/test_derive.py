"""Tests for deterministic metric derivation.

The cases here are the ones the grounding guard cares about: a derived figure
must be exactly reproducible, and an undefined one must be absent with a reason
rather than present as inf, NaN or a silent zero.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from rrip.ai.derive import derive
from rrip.ai.narration import collect_allowed, validate_grounding

WEEKLY = (
    [{"week_no": 1, "revenue": Decimal("800.00")},
     {"week_no": 2, "revenue": Decimal("1000.00")}],
    ["week_no", "revenue"],
)


def test_percent_change_is_computed_not_left_to_the_model():
    d = derive(*WEEKLY).data
    change = d["change_over_period"]["revenue"]
    assert change["absolute_change"] == 200.0
    assert change["percent_change"] == 25.0
    assert change["direction"] == "increase"


def test_the_derived_percentage_passes_the_grounding_guard():
    """The whole point: 'grew 25%' is now a grounded statement.

    Against the raw rows it is not -- 25 appears nowhere in {800, 1000} -- which
    is the rejection this module exists to remove.
    """
    d = derive(*WEEKLY).data
    assert not validate_grounding("Revenue grew 25% to 1,000.00.", d)

    # Against the raw rows, only the derived 25 is ungrounded -- 1,000.00 is
    # present. That it is present at all depends on collect_allowed handling
    # Decimal, which it did not until this test found otherwise; see
    # test_grounding_decimal.py.
    raw = {"columns": WEEKLY[1], "rows": WEEKLY[0]}
    violations = validate_grounding("Revenue grew 25% to 1,000.00.", raw)
    assert [v.value for v in violations] == [25.0]


def test_zero_baseline_yields_no_percentage_and_says_why():
    rows = [{"week_no": 1, "revenue": Decimal("0")},
            {"week_no": 2, "revenue": Decimal("500")}]
    change = derive(rows, ["week_no", "revenue"]).data["change_over_period"]["revenue"]
    assert change["percent_change"] is None
    assert "zero baseline" in change["percent_change_unavailable"]
    assert change["absolute_change"] == 500.0


def test_no_infinity_or_nan_reaches_the_payload():
    """A NaN in the payload is a number that will be printed at a reader."""
    rows = [{"week_no": 1, "revenue": Decimal("0")},
            {"week_no": 2, "revenue": Decimal("0")}]
    text = str(derive(rows, ["week_no", "revenue"]).data)
    assert "inf" not in text.lower()
    assert "nan" not in text.lower()


def test_negative_change_is_reported_as_a_decrease():
    rows = [{"week_no": 1, "revenue": Decimal("1000")},
            {"week_no": 2, "revenue": Decimal("750")}]
    change = derive(rows, ["week_no", "revenue"]).data["change_over_period"]["revenue"]
    assert change["absolute_change"] == -250.0
    assert change["percent_change"] == -25.0
    assert change["direction"] == "decrease"


def test_nulls_are_excluded_not_treated_as_zero():
    rows = [{"dept": "A", "revenue": Decimal("100")},
            {"dept": "B", "revenue": None},
            {"dept": "C", "revenue": Decimal("300")}]
    d = derive(rows, ["dept", "revenue"]).data
    assert d["totals"]["revenue"]["total"] == 400.0
    assert d["totals"]["revenue"]["mean"] == 200.0      # not 133.33
    assert d["null_counts"] == {"revenue": 1}


def test_shares_sum_to_one_hundred():
    rows = [{"dept": "A", "revenue": Decimal("250")},
            {"dept": "B", "revenue": Decimal("750")}]
    shares = derive(rows, ["dept", "revenue"]).data["share_of_total"]["shares"]
    assert [s["pct_of_total"] for s in shares] == [25.0, 75.0]


def test_shares_are_not_computed_when_the_measure_is_ambiguous():
    """Two measures means 'share' has no single meaning, so it is not offered."""
    rows = [{"dept": "A", "revenue": Decimal("250"), "baskets": 10},
            {"dept": "B", "revenue": Decimal("750"), "baskets": 30}]
    assert "share_of_total" not in derive(rows, ["dept", "revenue", "baskets"]).data


def test_zero_total_produces_no_shares():
    rows = [{"dept": "A", "revenue": Decimal("0")},
            {"dept": "B", "revenue": Decimal("0")}]
    d = derive(rows, ["dept", "revenue"]).data
    assert "share_of_total" not in d
    assert any("undefined" in n for n in d["notes"])


def test_empty_result_is_described_rather_than_crashing():
    d = derive([], ["revenue"]).data
    assert d["row_count"] == 0
    assert any("no rows" in n for n in d["notes"])


def test_a_column_with_one_string_is_not_treated_as_a_measure():
    rows = [{"k": "a", "v": 1}, {"k": "b", "v": "n/a"}]
    assert "totals" not in derive(rows, ["k", "v"]).data


def test_highest_and_lowest_rows_are_named():
    rows = [{"dept": "A", "revenue": Decimal("100")},
            {"dept": "B", "revenue": Decimal("900")}]
    t = derive(rows, ["dept", "revenue"]).data["totals"]["revenue"]
    assert t["highest_row"] == {"dept": "B", "revenue": 900.0}
    assert t["lowest_row"] == {"dept": "A", "revenue": 100.0}


def test_truncation_is_disclosed_and_totals_still_cover_everything():
    rows = [{"week_no": i, "revenue": Decimal("1")} for i in range(1, 301)]
    d = derive(rows, ["week_no", "revenue"]).data
    assert d["rows_truncated_to"] == 200
    assert d["totals"]["revenue"]["total"] == 300.0
    assert any("computed over ALL rows" in n for n in d["notes"])


@pytest.mark.parametrize("value", [Decimal("45.634"), Decimal("45.635"), 45.6])
def test_rounded_forms_of_a_derived_value_are_grounded(value):
    """The guard admits roundings of anything in the payload, including derived."""
    d = derive([{"dept": "A", "revenue": value}], ["dept", "revenue"]).data
    allowed = collect_allowed(d)
    assert round(float(value), 1) in allowed
