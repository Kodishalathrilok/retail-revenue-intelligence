"""The forecasting contract must stay internally consistent.

These tests need no database and no artifact. They check that the specification
does not contradict itself -- which matters because every other module derives
its behaviour from it, so an inconsistency here is silently inherited
everywhere.
"""

from __future__ import annotations

import pytest

from rrip.forecast import contract as C


def test_splits_are_ordered_contiguous_and_disjoint():
    tr, va, te = C.TRAIN_WEEKS, C.VALIDATION_WEEKS, C.TEST_WEEKS
    assert tr[0] < tr[1] < va[0] < va[1] < te[0] < te[1]
    assert va[0] == tr[1] + 1, "a gap between train and validation wastes weeks"
    assert te[0] == va[1] + 1, "a gap between validation and test wastes weeks"


def test_first_target_week_leaves_room_for_the_deepest_window():
    """No feature may reach below the history floor for the earliest target.

    This is the arithmetic behind the panel-ramp guard. If MAX_LOOKBACK_WEEKS
    grows without FIRST_TARGET_WEEK moving, the deepest rolling window silently
    starts reading enrolment weeks again.
    """
    assert C.FIRST_TARGET_WEEK == C.HISTORY_FLOOR_WEEK + C.MAX_LOOKBACK_WEEKS
    deepest = min(f.min_week_offset for f in C.FEATURES)
    oldest_week_read = (C.FIRST_TARGET_WEEK - 1) + deepest
    assert oldest_week_read >= C.HISTORY_FLOOR_WEEK


def test_no_feature_declares_an_offset_after_the_cutoff():
    offenders = [f.name for f in C.FEATURES if f.max_week_offset > 0]
    assert not offenders, (
        f"{offenders} declare that they read a week after the prediction "
        "cutoff, which is the definition of leakage")


def test_feature_offsets_are_ordered():
    for f in C.FEATURES:
        assert f.min_week_offset <= f.max_week_offset, f.name


def test_partial_weeks_are_outside_the_target_range():
    for w in C.PARTIAL_WEEKS:
        assert not (C.FIRST_TARGET_WEEK <= w <= C.LAST_TARGET_WEEK), (
            f"week {w} is partial (5 or 6 days) and must not be a target")


def test_feature_names_are_unique():
    names = [f.name for f in C.FEATURES]
    assert len(set(names)) == len(names)
    assert tuple(names) == C.FEATURE_NAMES


def test_split_of_covers_every_target_week_exactly_once():
    for w in range(C.FIRST_TARGET_WEEK, C.LAST_TARGET_WEEK + 1):
        assert C.split_of(w) in {"train", "validation", "test"}, w
    assert C.split_of(C.FIRST_TARGET_WEEK - 1) == "none"
    assert C.split_of(C.LAST_TARGET_WEEK + 1) == "none"


def test_seasonal_lag_is_the_measured_one_not_the_conventional_one():
    """Lag 52 is rejected on purpose; see the contract's seasonality note."""
    assert C.SEASONAL_LAG_WEEKS == 4
    assert C.REJECTED_SEASONAL_LAG_WEEKS == 52
    # Lag 52 could not be a feature even if it helped: it would reach below
    # the floor for the earliest target.
    assert (C.FIRST_TARGET_WEEK - C.REJECTED_SEASONAL_LAG_WEEKS
            < C.HISTORY_FLOOR_WEEK)


def test_only_one_step_ahead_is_supported():
    assert C.SUPPORTED_HORIZONS == (1,)
    assert C.HORIZON_WEEKS in C.SUPPORTED_HORIZONS


def test_contract_serialises_with_every_block_the_artifact_needs():
    d = C.CONTRACT.to_dict()
    assert d["contract_version"] == C.CONTRACT_VERSION
    assert d["target"]["name"] == C.TARGET_NAME
    assert d["windows"]["train"] == list(C.TRAIN_WEEKS)
    assert d["windows"]["test"] == list(C.TEST_WEEKS)
    assert d["features"]["names"] == list(C.FEATURE_NAMES)
    assert d["missing_data_policy"]
    assert d["retraining_policy"]


@pytest.mark.parametrize("feature", C.FEATURES, ids=lambda f: f.name)
def test_every_feature_is_documented(feature):
    """A registry entry with no reason is a feature nobody can audit."""
    assert len(feature.description) > 20, feature.name
    assert feature.family
