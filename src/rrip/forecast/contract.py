"""The forecasting contract: what is predicted, from what, and where the line is.

This module is the specification, not a helper. Every other module in
rrip.forecast reads its windows, its feature registry and its cutoff rule from
here, so that "could this feature have been known at prediction time?" has
exactly one answer and it is checkable by a program (see rrip.forecast.leakage).

WHY THIS TARGET

The candidate targets were weighed against the data rather than against
modelling convenience, and two of them are ruled out by measurement:

  * Household-level future spend. 2,500 households x 74 usable weeks looks like
    a large panel, but weekly household spend is mostly zeros and the panel is
    a closed cohort -- there is no acquisition process to learn. It would be a
    bigger table, not a better question.

  * Daily revenue. dim_date carries DAY 1..711, but this dataset's calendar
    anchor is arbitrary (docs/schema.md) and the semantic layer declares
    day-of-week an ABSENT domain: weekday labels are a modelling convention.
    Daily forecasting without a real weekday is forecasting a series whose
    dominant seasonality has been declared unknowable. Refusing to model it is
    consistent with the rest of the project rather than an omission.

Department x week revenue survives because it is the grain a supermarket
actually plans on -- category budgets, labour, space -- it is already the grain
of the published tier (pub_weekly_revenue_by_dept), and it has a clean temporal
structure with enough series to pool across.

THE PANEL RAMP, WHICH IS THE REAL HAZARD HERE

dunnhumby recruits households at the start of observation. Measured on the
loaded data: 17.9% of the panel has transacted by week 4, 63.5% by week 12,
90.8% by week 16, 99.7% by week 20. Total weekly revenue therefore quadruples
over the first 20 weeks and none of that is demand -- it is enrolment.

A model trained across that boundary learns a growth rate that does not exist,
and every rolling feature computed across it is a statistic of two different
populations. So HISTORY_FLOOR_WEEK is a hard floor: no feature, for any target
week, may read a week before it. That is stronger than "start training later",
because a rolling mean at the start of training would otherwise reach back
into the ramp.

WHAT IS DELIBERATELY NOT A FEATURE

Anything dated after the prediction cutoff, without exception -- including
promotion and campaign activity in the target week. A real retailer knows next
week's mailer, so this is a stricter line than production would need. It is
drawn here because "we would have known it" is an assumption about an operating
process that this dataset cannot evidence, and a leakage boundary that depends
on an unverifiable claim is not a boundary. rrip.forecast.leakage measures what
that restraint costs by scoring an oracle variant that breaks it; the number is
in the release report, and the oracle is never servable.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# The version stamped into every artifact and every API response. Bump it when
# the feature set or the windows change, because a stored model whose features
# no longer mean what its metadata says is worse than no model.
CONTRACT_VERSION = "forecast_v1"

TARGET_NAME = "weekly_department_revenue"
TARGET_EXPRESSION = "sum(sales_value)"
TARGET_GRAIN = "department x week"
TARGET_UNIT = "USD"

# One week ahead, and only one. Multi-step is not supported rather than
# silently approximated: every feature here is a lag or a rolling window over
# observed revenue, and at h=2 the most informative of them (last week's actual)
# does not exist. Iterating the model over its own output would produce a
# number the honest error bars for which have not been measured. The API
# rejects h != 1 (see rrip.api.forecast_routes).
HORIZON_WEEKS = 1
SUPPORTED_HORIZONS = (1,)

# ---------------------------------------------------------------------------
# Temporal windows
# ---------------------------------------------------------------------------
#
# All bounds are inclusive week numbers in dim_week's 1..102 space.

# Weeks 1 and 102 span 5 and 6 days; dim_week.is_partial_week flags them. They
# are excluded from targets AND from feature history, because a partial week
# entering a rolling mean drags it down for the following seven target weeks.
PARTIAL_WEEKS = (1, 102)

# No feature may read a week before this. See the module note on the ramp.
HISTORY_FLOOR_WEEK = 20

# The longest lookback any feature uses. Target weeks start at
# HISTORY_FLOOR_WEEK + MAX_LOOKBACK_WEEKS so the deepest window still lands on
# or after the floor: for target week 28 the 8-week window is weeks 20..27.
#
# A 13-week window was measured and rejected -- it scored WAPE 8.01% against
# 7.93% for the 8-week window, so it costs five target weeks of training data
# to make the forecast slightly worse.
MAX_LOOKBACK_WEEKS = 8

FIRST_TARGET_WEEK = HISTORY_FLOOR_WEEK + MAX_LOOKBACK_WEEKS   # 28
LAST_TARGET_WEEK = 101                                        # 102 is partial

# Chronological split. Sizes are 46/14/14 target weeks.
#
# WHY RANDOM SPLITTING WOULD BE INVALID HERE, CONCRETELY
#
# Not merely "because it is a time series". Three specific mechanisms:
#
#   1. The features ARE the neighbouring rows. roll8 for target week t is the
#      mean of weeks t-8..t-1. Randomly assigning week t to test and week t-1
#      to train puts the test row's target inside a training row's feature
#      window -- the training set literally contains the test answer, averaged
#      with seven others.
#   2. The inclusion rule and the normalisation scale are fitted on training
#      data. Under a random split those are fitted on data surrounding the test
#      weeks, so the preprocessing has seen the future even if the model has not.
#   3. It would answer the wrong question. Interpolating a held-out week from
#      the weeks either side of it is not the task; a planner has only the past.
#      A random split reports interpolation accuracy and calls it forecasting.
#
# The test range is untouched during model and hyperparameter selection --
# rrip.forecast.split raises if a caller asks for it before the selection is
# frozen.
TRAIN_WEEKS = (FIRST_TARGET_WEEK, 73)
VALIDATION_WEEKS = (74, 87)
TEST_WEEKS = (88, LAST_TARGET_WEEK)

# Rolling-origin folds inside TRAIN+VALIDATION, used for hyperparameter choice.
# Each fold trains on everything up to its origin and validates on the next
# ORIGIN_STEP weeks, so every fold is a real forecast.
ORIGIN_STEP = 7
N_ORIGINS = 6

# ---------------------------------------------------------------------------
# Series inclusion
# ---------------------------------------------------------------------------
#
# Fitted on TRAIN weeks only. A rule computed over the whole panel would use
# test-period activity to decide which departments are modellable, which is a
# quiet form of leakage: it is selection on the outcome.
#
# The thresholds are deliberately loose. Tightening them to the 17 departments
# with full coverage would remove exactly the sparse, awkward series that the
# robustness suite exists to interrogate, and reporting a clean aggregate
# number after deleting the hard cases is the failure this project is written
# against.
MIN_TRAIN_MEAN_REVENUE = 1.0     # USD per week, averaged over training weeks
MIN_TRAIN_NONZERO_FRAC = 0.5     # at least half the training weeks had sales

# Revenue is dense by definition: a department with no sales in a week earned
# zero, it is not missing. The panel is therefore built as a CROSS JOIN of
# qualifying departments with weeks and LEFT JOINed to observed revenue, with
# absent rows coalesced to 0. Treating those as NaN and imputing them would
# invent sales that did not happen.
MISSING_REVENUE_POLICY = "absent department-week means zero revenue, not missing"

# ---------------------------------------------------------------------------
# Target transformation
# ---------------------------------------------------------------------------
#
# Departments span three orders of magnitude (GROCERY ~$44k/week, FROZEN
# GROCERY ~$8/week). A pooled model on raw dollars spends its capacity learning
# which series is which, and its error is whatever GROCERY's error is.
#
# So the model predicts a RATIO to a scale known at prediction time:
#
#     scale s(d,t)  = mean revenue for department d over weeks t-7..t
#     model target  = revenue(d, t+1) / s(d,t)
#     forecast      = predicted_ratio * s(d,t)
#
# Two properties make this worth the indirection:
#
#   * s(d,t) uses only observed history, so the transformation cannot leak.
#   * Predicting the constant 1.0 reproduces the trailing-mean baseline exactly.
#     The model therefore starts level with the strongest baseline and any gain
#     is a measured improvement on it, not an artefact of scaling.
#
# Training weights each row by s(d,t). Weighted absolute error in ratio space is
# dollar absolute error, so the training objective is the reported metric rather
# than a proxy for it. The cost is that sparse departments carry almost no
# weight -- that is not hidden, it is measured per department in the robustness
# suite and it drives the LIMITED confidence flag the API returns.
SCALE_WINDOW_WEEKS = 8
SCALE_FLOOR_USD = 1.0

# A series whose trailing scale sits at the floor has no usable history to
# forecast from. The service refuses rather than extrapolating (STEP 17).
MIN_SERVABLE_SCALE_USD = 5.0

# ---------------------------------------------------------------------------
# Seasonality
# ---------------------------------------------------------------------------
#
# The grain is already weekly, so the candidate seasonal period is annual --
# lag 52. It was tested and it does not work, for two independent reasons:
#
#   * Measurement. A lag-52 seasonal-naive baseline scores WAPE 8.72% against
#     7.93% for an 8-week trailing mean, on the department panel. It is worse
#     than having no seasonal term at all.
#   * Structure. The panel is 102 weeks. Lag 52 exists only for target weeks
#     80..101, and for those it points back into weeks 28..49 -- so a
#     "year-ago" comparison is available for under a third of the window.
#
# What the data does show is a four-week cycle: lag-4 autocorrelation is +0.36
# on total revenue post-ramp, and +0.43 for GROCERY, +0.52 for MEAT-PCKGD,
# +0.43 for PRODUCE. Consistent with monthly household budgeting. So the
# seasonal-naive baseline in this project is lag 4, chosen from the measured
# autocorrelation rather than from convention, and lag 52 is retained in the
# report as a recorded negative.
SEASONAL_LAG_WEEKS = 4
REJECTED_SEASONAL_LAG_WEEKS = 52

# ---------------------------------------------------------------------------
# Feature registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Feature:
    """One model input, with the fact that makes it legal.

    `max_week_offset` is the newest week the feature reads, expressed relative
    to the prediction cutoff t (where the target is week t+1). It must be <= 0.
    An offset of 0 means "uses week t", the last fully observed week. +1 would
    mean the feature reads the target week, and rrip.forecast.leakage fails the
    build if any registered feature declares one.

    `min_week_offset` is the oldest week it reads, used to prove that no
    feature reaches below HISTORY_FLOOR_WEEK for the earliest target.
    """

    name: str
    description: str
    max_week_offset: int
    min_week_offset: int
    family: str
    # Set when the feature is a ratio to the trailing scale, which is most of
    # them. Recorded so the leakage audit can check the scale window too.
    scaled: bool = True


FEATURES: tuple[Feature, ...] = (
    # --- recent level, normalised by the trailing scale -------------------
    Feature("rev_lag1_ratio", "Revenue in week t, over trailing scale.",
            0, 0, "lag"),
    Feature("rev_lag2_ratio", "Revenue in week t-1, over trailing scale.",
            -1, -1, "lag"),
    Feature("rev_lag3_ratio", "Revenue in week t-2, over trailing scale.",
            -2, -2, "lag"),
    Feature("rev_lag4_ratio", "Revenue in week t-3 -- the seasonal lag, since "
                              "the target is t+1 and the cycle is 4 weeks.",
            -3, -3, "seasonal"),

    # --- shape of the recent window --------------------------------------
    Feature("roll4_ratio", "Mean of weeks t-3..t over the 8-week scale. Above "
                           "1 means the short window is running hot.",
            0, -3, "rolling"),
    Feature("rollmed8_ratio", "Median of weeks t-7..t over the scale. Differs "
                              "from 1 when the window holds an outlier.",
            0, -7, "rolling"),
    Feature("rollstd8_ratio", "Standard deviation of weeks t-7..t over the "
                              "scale -- the series' own coefficient of "
                              "variation, so the model can learn that a noisy "
                              "series should be pulled harder to its mean.",
            0, -7, "rolling"),
    Feature("momentum_ratio", "roll4 minus roll8, over the scale. Short-term "
                              "drift away from the longer level.",
            0, -7, "rolling"),
    Feature("trend_slope_ratio", "OLS slope across weeks t-7..t, over the "
                                 "scale. Distinguishes a rising series from a "
                                 "flat one with the same mean.",
            0, -7, "rolling"),
    Feature("zeros_in_window", "Count of zero-revenue weeks in t-7..t. The "
                               "sparse-series signal.",
            0, -7, "rolling", scaled=False),

    # --- panel context, all observed ---------------------------------------
    #
    # Department revenue co-moves with how busy the whole panel was. These are
    # aggregates over every department INCLUDING this one, which is fine at
    # week <= t because the department's own week-t revenue is already a
    # feature; nothing here is dated after the cutoff.
    Feature("panel_rev_lag1_ratio", "Panel-wide revenue in week t over its own "
                                    "8-week trailing mean.",
            0, -7, "panel"),
    Feature("panel_hh_lag1_ratio", "Active households panel-wide in week t "
                                   "over their 8-week trailing mean.",
            0, -7, "panel"),
    Feature("panel_baskets_lag1_ratio", "Panel-wide baskets in week t over "
                                        "their 8-week trailing mean.",
            0, -7, "panel"),
    Feature("dept_share_lag1", "This department's share of panel revenue in "
                               "week t.",
            0, 0, "panel", scaled=False),
    Feature("dept_share_delta", "Share in week t minus mean share over "
                                "t-7..t.",
            0, -7, "panel", scaled=False),

    # --- promotion exposure, strictly lagged --------------------------------
    #
    # From fact_causal, which is product x store x week and covers weeks 9..101.
    # Aggregated to department x week. Week t only -- see the module note on
    # why next week's mailer is refused even though a retailer would know it.
    Feature("promo_display_pct_lag1", "Share of this department's promo rows in "
                                      "week t that were on in-store display.",
            0, 0, "promo", scaled=False),
    Feature("promo_mailer_pct_lag1", "Share of this department's promo rows in "
                                     "week t that appeared in a mailer.",
            0, 0, "promo", scaled=False),
    Feature("promo_display_delta", "Display share in week t minus its mean "
                                   "over t-7..t. Promotion intensity is "
                                   "persistent, so the change carries more "
                                   "than the level.",
            0, -7, "promo", scaled=False),
    Feature("promo_rows_ratio", "Promoted product-store rows for this "
                                "department in week t over their 8-week mean.",
            0, -7, "promo", scaled=False),

    # --- campaign context, strictly lagged ----------------------------------
    Feature("campaigns_active_lag1", "Marketing campaigns running in week t.",
            0, 0, "campaign", scaled=False),
    Feature("campaign_households_lag1", "Households enrolled in a campaign "
                                        "running in week t.",
            0, 0, "campaign", scaled=False),

    # --- series identity ----------------------------------------------------
    #
    # The department itself, target-encoded from TRAINING rows only. Lets the
    # model learn that KIOSK-GAS is persistent (lag-1 autocorrelation +0.72)
    # while COSMETICS is not (+0.03), which is the specific thing a single
    # fixed baseline cannot do and the main reason to expect any gain at all.
    Feature("dept_volatility", "Coefficient of variation of this department's "
                               "weekly revenue, over training weeks only.",
            0, -7, "identity", scaled=False),
    Feature("dept_persistence", "Lag-1 autocorrelation of this department's "
                                "weekly revenue, over training weeks only.",
            0, -7, "identity", scaled=False),
    Feature("dept_log_scale", "log10 of the department's mean training-week "
                              "revenue. Size, not level -- the level is "
                              "already divided out.",
            0, -7, "identity", scaled=False),
)

FEATURE_NAMES: tuple[str, ...] = tuple(f.name for f in FEATURES)

# Bumped whenever FEATURES changes. The registry refuses to load an artifact
# whose feature version does not match the running code, because a silent
# column reorder is a wrong prediction that looks completely normal.
FEATURE_VERSION = "1.0.0"


@dataclass(frozen=True)
class Contract:
    """The whole specification as one value, for stamping into artifacts."""

    version: str = CONTRACT_VERSION
    target_name: str = TARGET_NAME
    target_expression: str = TARGET_EXPRESSION
    grain: str = TARGET_GRAIN
    unit: str = TARGET_UNIT
    horizon_weeks: int = HORIZON_WEEKS
    prediction_time: str = "end of week t; the target is week t+1"
    history_floor_week: int = HISTORY_FLOOR_WEEK
    first_target_week: int = FIRST_TARGET_WEEK
    last_target_week: int = LAST_TARGET_WEEK
    train_weeks: tuple[int, int] = TRAIN_WEEKS
    validation_weeks: tuple[int, int] = VALIDATION_WEEKS
    test_weeks: tuple[int, int] = TEST_WEEKS
    feature_version: str = FEATURE_VERSION
    feature_names: tuple[str, ...] = FEATURE_NAMES
    scale_window_weeks: int = SCALE_WINDOW_WEEKS
    seasonal_lag_weeks: int = SEASONAL_LAG_WEEKS
    missing_policy: str = MISSING_REVENUE_POLICY
    partial_weeks: tuple[int, ...] = PARTIAL_WEEKS
    # The panel ended at day 711 and the source does not change, so there is
    # nothing to retrain on. Recorded as an explicit policy rather than left
    # unstated: on live data this model would retrain weekly, refitting the
    # conformal residual quantiles on the most recent completed weeks.
    retraining_policy: str = (
        "Static dataset: the panel ends at week 102 and does not grow, so the "
        "artifact is rebuilt only when code or contract changes. On a live "
        "feed the equivalent cadence is weekly, refitting both the model and "
        "the conformal residual quantiles on a rolling window.")
    notes: tuple[str, ...] = field(default_factory=lambda: (
        "Weeks 1 and 102 are partial (5 and 6 days) and are excluded entirely.",
        "No feature reads a week before 20; 99.7% of the panel had transacted "
        "by then, and earlier weeks are enrolment rather than demand.",
        "No feature reads week t+1, including promotion and campaign activity, "
        "even though a retailer would know next week's mailer.",
        "Annual seasonality is not modelled: lag 52 exists for under a third "
        "of the window and scored worse than an 8-week trailing mean.",
    ))

    def to_dict(self) -> dict:
        return {
            "contract_version": self.version,
            "target": {
                "name": self.target_name,
                "expression": self.target_expression,
                "grain": self.grain,
                "unit": self.unit,
            },
            "horizon_weeks": self.horizon_weeks,
            "prediction_time": self.prediction_time,
            "windows": {
                "history_floor_week": self.history_floor_week,
                "first_target_week": self.first_target_week,
                "last_target_week": self.last_target_week,
                "train": list(self.train_weeks),
                "validation": list(self.validation_weeks),
                "test": list(self.test_weeks),
                "partial_weeks_excluded": list(self.partial_weeks),
            },
            "features": {
                "version": self.feature_version,
                "names": list(self.feature_names),
                "scale_window_weeks": self.scale_window_weeks,
                "seasonal_lag_weeks": self.seasonal_lag_weeks,
            },
            "missing_data_policy": self.missing_policy,
            "retraining_policy": self.retraining_policy,
            "notes": list(self.notes),
        }


CONTRACT = Contract()


def week_in(window: tuple[int, int], week: int) -> bool:
    return window[0] <= week <= window[1]


def split_of(week: int) -> str:
    """Which split a target week belongs to. 'none' if outside the panel."""
    if week_in(TRAIN_WEEKS, week):
        return "train"
    if week_in(VALIDATION_WEEKS, week):
        return "validation"
    if week_in(TEST_WEEKS, week):
        return "test"
    return "none"
