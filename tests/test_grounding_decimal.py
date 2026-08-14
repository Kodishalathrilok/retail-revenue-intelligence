"""REGRESSION: the grounding guard was blind to every Postgres NUMERIC value.

`collect_allowed` walked the input with `isinstance(node, (int, float))`.
Decimal is neither. Postgres returns every NUMERIC column -- sales_value,
revenue, every monetary figure in this project -- as Decimal, so those values
never entered the allowed set. The guard then rejected narratives whose numbers
came straight out of the data it was given.

The failure was invisible from the outside because rejection is the guard's
correct-looking behaviour: the API returned `ok: false, rejected_reason:
grounding guard rejected 2 ungrounded number(s)` and every part of that sentence
reads like the system working. It was found by asserting the opposite -- that a
true statement about a Decimal input must produce no violations.

These tests pin the whole numeric-type surface, because the same blindness would
return the moment someone narrows the isinstance check again.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from rrip.ai.narration import collect_allowed, validate_grounding


def test_decimal_values_enter_the_allowed_set():
    allowed = collect_allowed({"revenue": Decimal("1000.00")})
    assert 1000.0 in allowed


def test_a_true_statement_about_decimal_data_is_not_rejected():
    data = {"rows": [{"week_no": 1, "revenue": Decimal("800.00")},
                     {"week_no": 2, "revenue": Decimal("1000.00")}]}
    assert validate_grounding("Revenue rose from 800.00 to 1,000.00.", data) == []


def test_an_invented_number_is_still_rejected_alongside_decimals():
    """The fix must not turn the guard off."""
    data = {"rows": [{"revenue": Decimal("1000.00")}]}
    violations = validate_grounding("Revenue was 1,000.00, up from 640.00.", data)
    assert [v.value for v in violations] == [640.0]


@pytest.mark.parametrize("value,mentioned", [
    (Decimal("1000.00"), "1000"),
    (Decimal("1000.00"), "1,000.00"),
    (Decimal("0.5"), "0.5"),
    (Decimal("-250.75"), "-250.75"),
    (Decimal("1234567.89"), "1,234,567.89"),
    (Decimal("45.634"), "45.63"),
    (Decimal("45.634"), "45.6"),
])
def test_decimal_roundings_and_formattings_are_grounded(value, mentioned):
    data = {"v": value}
    assert validate_grounding(f"The figure is {mentioned}.", data) == [], mentioned


def test_mixed_int_float_and_decimal_all_land_in_the_allowed_set():
    allowed = collect_allowed({"a": 7, "b": 2.5, "c": Decimal("3.25")})
    assert {7.0, 2.5, 3.25} <= allowed


def test_decimal_inside_nested_structures_is_reached():
    data = {"totals": {"revenue": {"total": Decimal("42.00")}},
            "rows": [[Decimal("13.00")]]}
    allowed = collect_allowed(data)
    assert 42.0 in allowed
    assert 13.0 in allowed


def test_booleans_are_still_not_treated_as_numbers():
    """True must not ground the number 1 -- that would admit an invented 1."""
    allowed = collect_allowed({"flag": True, "other": False})
    assert 1.0 not in allowed
    assert 0.0 not in allowed
