"""Feature construction, with the prediction cutoff enforced structurally.

THE ONE RULE

A row in the output frame is a TARGET: department d, week w. Every feature on
that row is built from the panel shifted by at least one week, so it can only
ever read weeks <= w-1. There is no code path that indexes week w on a target
row for week w -- not "we are careful not to", but "the frame the features are
computed from has been shifted first".

That matters more than it sounds, because the natural way to write this is to
compute lags on the panel and then join the target on. Doing it in that order
puts an unshifted `revenue` column next to the target for the whole of feature
engineering, and one autocomplete away from a model that scores beautifully.

WHY CONTIGUITY IS ASSERTED

pandas' shift(1) means "the previous ROW", not "the previous WEEK". Those are
the same thing only while the week series has no gaps. Weeks 1 and 102 are
partial and excluded, and if either had fallen inside the modelling window --
or if a department were built from observed rows instead of a dense cross join
-- shift(1) would quietly reach two weeks back and every lag feature would be
mislabelled while still looking entirely reasonable. So the gap check runs on
every build and raises.

THE FITTED FEATURES

dept_volatility, dept_persistence and dept_log_scale are per-department
constants summarising how that series behaves. They are fitted on TRAINING
weeks only and then applied to validation and test unchanged, exactly like a
target encoder. Computing them over the full panel is the subtle leak in this
module: no individual feature would reference a future week, but the encoding
of "this department is volatile" would have been informed by the test period.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from rrip.forecast import contract as C

# The 8-week window's OLS slope reduces to a fixed linear filter: with x =
# 0..7, the denominator sum((x-xbar)^2) is a constant 42, so only the numerator
# weights vary. Precomputed rather than refitted per window.
_SLOPE_X = np.arange(C.SCALE_WINDOW_WEEKS, dtype=float)
_SLOPE_W = (_SLOPE_X - _SLOPE_X.mean())
_SLOPE_DEN = float((_SLOPE_W ** 2).sum())


class ContiguityError(ValueError):
    """A department's week series has a gap, so lag features would be wrong."""


def assert_contiguous_weeks(panel: pd.DataFrame) -> None:
    """Every department must cover the same unbroken run of weeks.

    Raises rather than repairing. A gap means the panel was built wrongly
    upstream, and silently reindexing it here would hide that while producing
    lag features whose names no longer describe their contents.
    """
    weeks = np.sort(panel.week_no.unique())
    gaps = np.diff(weeks)
    if len(gaps) and not (gaps == 1).all():
        missing = sorted(set(range(int(weeks[0]), int(weeks[-1]) + 1))
                         - set(int(w) for w in weeks))
        raise ContiguityError(
            f"panel weeks are not contiguous; missing {missing}. Lag features "
            "shift by row, so a gap silently changes what every lag means.")

    expected = len(weeks)
    counts = panel.groupby("department").week_no.nunique()
    bad = counts[counts != expected]
    if len(bad):
        raise ContiguityError(
            f"{len(bad)} departments do not span all {expected} weeks "
            f"({bad.head(5).to_dict()}). The panel must be dense -- a "
            "department with no sales in a week earned zero, and dropping the "
            "row shortens its lag windows without saying so.")


def _safe_ratio(num: pd.Series, den: pd.Series, floor: float) -> pd.Series:
    return num / den.clip(lower=floor)


def _lag(df: pd.DataFrame, col: str, periods: int = 1) -> pd.Series:
    return df.groupby("department", sort=False)[col].shift(periods)


def _rolling(df: pd.DataFrame, col: str, window: int, how: str) -> pd.Series:
    """A rolling statistic over the window ENDING at the previous week.

    Two properties this has to get right, and both were once wrong here:

    THE SHIFT COMES FIRST. shift(1) is applied before the window, not after, so
    the window for target week w is weeks w-window..w-1 and cannot include w.

    THE ROLLING IS GROUPED. The obvious spelling --
    `df.groupby("department")[col].shift(1).rolling(window)` -- does NOT do
    this. SeriesGroupBy.shift returns a plain Series, so the rolling that
    follows runs across the whole frame and each department's first windows are
    filled with the tail of the previous department.

    The leakage audit caught that on its first run, via a route worth recording:
    the mean and median were unaffected in the delivered rows, because the
    contaminated windows all land in weeks 20-27 which are dropped before the
    first target week. The VARIANCE was affected everywhere, because pandas
    computes rolling variance with an add/remove accumulator -- once a value
    from another department has passed through it, the running sum of squares
    carries the rounding error for the rest of the series. The audit's
    corrupt-a-week probe multiplied one week by 1000 and rollstd8_ratio moved
    by 7e-05 relative, in departments whose own windows had not changed at all.

    tests/test_forecast_leakage.py::test_rolling_features_are_grouped_by_series
    is the permanent regression test for it.
    """
    shifted = df.groupby("department", sort=False)[col].shift(1)
    grouped = shifted.groupby(df.department, sort=False).rolling(
        window, min_periods=window)

    if how == "std":
        # numpy's std is two-pass and exact. pandas' rolling std is a streaming
        # accumulator whose error depends on values that have already left the
        # window, which makes it non-reproducible under any reordering -- an
        # unacceptable property for a feature that has to be provably a
        # function of one department's own history.
        out = grouped.apply(lambda a: float(np.std(a, ddof=1)), raw=True)
    else:
        out = getattr(grouped, how)()

    return out.reset_index(level=0, drop=True).reindex(df.index)


def _rolling_slope(df: pd.DataFrame, col: str, window: int) -> pd.Series:
    def slope(a: np.ndarray) -> float:
        return float((_SLOPE_W * (a - a.mean())).sum() / _SLOPE_DEN)

    shifted = df.groupby("department", sort=False)[col].shift(1)
    out = (shifted.groupby(df.department, sort=False)
           .rolling(window, min_periods=window).apply(slope, raw=True))
    return out.reset_index(level=0, drop=True).reindex(df.index)


@dataclass
class FittedStats:
    """Per-department constants learned from training weeks only."""

    volatility: dict[str, float] = field(default_factory=dict)
    persistence: dict[str, float] = field(default_factory=dict)
    log_scale: dict[str, float] = field(default_factory=dict)
    departments: list[str] = field(default_factory=list)
    fitted_on_weeks: tuple[int, int] = C.TRAIN_WEEKS

    def to_dict(self) -> dict:
        return {
            "fitted_on_weeks": list(self.fitted_on_weeks),
            "departments": self.departments,
            "volatility": {k: round(v, 6) for k, v in self.volatility.items()},
            "persistence": {k: round(v, 6) for k, v in self.persistence.items()},
            "log_scale": {k: round(v, 6) for k, v in self.log_scale.items()},
        }

    @classmethod
    def from_dict(cls, d: dict) -> FittedStats:
        return cls(
            volatility=dict(d["volatility"]),
            persistence=dict(d["persistence"]),
            log_scale=dict(d["log_scale"]),
            departments=list(d["departments"]),
            fitted_on_weeks=tuple(d["fitted_on_weeks"]),  # type: ignore[arg-type]
        )


def fit_department_stats(panel: pd.DataFrame,
                         departments: list[str],
                         weeks: tuple[int, int] = C.TRAIN_WEEKS) -> FittedStats:
    """Summarise each series' behaviour, from `weeks` only.

    `persistence` is what makes the pooled model able to do something a single
    fixed baseline cannot: KIOSK-GAS carries lag-1 autocorrelation +0.72 and
    should be forecast from last week, COSMETICS carries +0.03 and should be
    forecast from its mean. One global rule has to pick one of those and be
    wrong for the other.
    """
    lo, hi = weeks
    sub = panel[(panel.week_no >= lo) & (panel.week_no <= hi)
                & panel.department.isin(departments)]

    vol: dict[str, float] = {}
    per: dict[str, float] = {}
    scale: dict[str, float] = {}

    for dept, g in sub.sort_values("week_no").groupby("department"):
        x = g.revenue.to_numpy(dtype=float)
        mean = float(x.mean())
        scale[dept] = float(np.log10(max(mean, 0.1)))
        vol[dept] = float(x.std(ddof=1) / mean) if mean > 0 and len(x) > 1 else 0.0

        centred = x - mean
        den = float((centred ** 2).sum())
        per[dept] = float((centred[1:] * centred[:-1]).sum() / den) if den > 0 else 0.0

    return FittedStats(volatility=vol, persistence=per, log_scale=scale,
                       departments=sorted(departments), fitted_on_weeks=weeks)


def build_features(panel: pd.DataFrame,
                   stats: FittedStats,
                   allow_future_promo: bool = False) -> pd.DataFrame:
    """Turn the dense panel into one row per (department, target week).

    Returns the contract's feature columns plus `revenue` (the target),
    `scale_usd` (the trailing scale used to normalise), `target_ratio`, and the
    identifying columns. Rows before the first target week are dropped because
    their windows would reach below the history floor.

    `allow_future_promo` is FALSE for anything that trains or serves. It exists
    only so rrip.forecast.leakage can build the deliberately-leaking variant and
    measure what the strict boundary costs -- a leakage test that never
    constructs a leak proves the code compiles, not that the guard works.
    """
    assert_contiguous_weeks(panel)

    df = panel[panel.department.isin(stats.departments)].copy()
    df = df.sort_values(["department", "week_no"]).reset_index(drop=True)

    # --- the trailing scale; every ratio below divides by this ---------------
    df["scale_usd"] = _rolling(df, "revenue", C.SCALE_WINDOW_WEEKS, "mean")
    scale = df.scale_usd.clip(lower=C.SCALE_FLOOR_USD)

    # --- lags ----------------------------------------------------------------
    for i in (1, 2, 3, 4):
        df[f"rev_lag{i}_ratio"] = _safe_ratio(
            _lag(df, "revenue", i), scale, C.SCALE_FLOOR_USD)

    # --- window shape --------------------------------------------------------
    roll4 = _rolling(df, "revenue", 4, "mean")
    df["roll4_ratio"] = _safe_ratio(roll4, scale, C.SCALE_FLOOR_USD)
    df["rollmed8_ratio"] = _safe_ratio(
        _rolling(df, "revenue", C.SCALE_WINDOW_WEEKS, "median"), scale,
        C.SCALE_FLOOR_USD)
    df["rollstd8_ratio"] = _safe_ratio(
        _rolling(df, "revenue", C.SCALE_WINDOW_WEEKS, "std"), scale,
        C.SCALE_FLOOR_USD)
    df["momentum_ratio"] = _safe_ratio(roll4 - df.scale_usd, scale, C.SCALE_FLOOR_USD)
    df["trend_slope_ratio"] = _safe_ratio(
        _rolling_slope(df, "revenue", C.SCALE_WINDOW_WEEKS), scale,
        C.SCALE_FLOOR_USD)

    df["_is_zero"] = (df.revenue <= 0).astype(float)
    df["zeros_in_window"] = _rolling(df, "_is_zero", C.SCALE_WINDOW_WEEKS, "sum")

    # --- panel context -------------------------------------------------------
    #
    # Computed once on the deduplicated week series and merged back, so the
    # rolling window is over 82 weeks rather than being recomputed identically
    # inside every department group.
    wk = (df[["week_no", "panel_revenue", "panel_baskets", "panel_households"]]
          .drop_duplicates("week_no").sort_values("week_no").set_index("week_no"))
    for src, out in (("panel_revenue", "panel_rev_lag1_ratio"),
                     ("panel_baskets", "panel_baskets_lag1_ratio"),
                     ("panel_households", "panel_hh_lag1_ratio")):
        prev = wk[src].shift(1)
        base = wk[src].shift(1).rolling(C.SCALE_WINDOW_WEEKS,
                                        min_periods=C.SCALE_WINDOW_WEEKS).mean()
        wk[out] = prev / base.clip(lower=1e-9)
    df = df.merge(wk[["panel_rev_lag1_ratio", "panel_baskets_lag1_ratio",
                      "panel_hh_lag1_ratio"]],
                  left_on="week_no", right_index=True, how="left")

    df["_share"] = df.revenue / df.panel_revenue.clip(lower=1e-9)
    df["dept_share_lag1"] = _lag(df, "_share")
    df["dept_share_delta"] = df.dept_share_lag1 - _rolling(
        df, "_share", C.SCALE_WINDOW_WEEKS, "mean")

    # --- promotions ----------------------------------------------------------
    #
    # promo_rows == 0 means the department had no promoted product-store rows
    # that week, so the display share is 0, not undefined.
    df["_display_pct"] = df.on_display / df.promo_rows.clip(lower=1)
    df["_mailer_pct"] = df.in_mailer / df.promo_rows.clip(lower=1)

    promo_shift = 0 if allow_future_promo else 1

    df["promo_display_pct_lag1"] = _lag(df, "_display_pct", promo_shift)
    df["promo_mailer_pct_lag1"] = _lag(df, "_mailer_pct", promo_shift)
    df["promo_display_delta"] = (df.promo_display_pct_lag1
                                 - _rolling(df, "_display_pct",
                                            C.SCALE_WINDOW_WEEKS, "mean"))
    promo_base = _rolling(df, "promo_rows", C.SCALE_WINDOW_WEEKS, "mean")
    df["promo_rows_ratio"] = (_lag(df, "promo_rows", promo_shift)
                              / promo_base.clip(lower=1.0))

    # --- campaigns -----------------------------------------------------------
    df["campaigns_active_lag1"] = _lag(df, "campaigns_active", promo_shift)
    df["campaign_households_lag1"] = _lag(df, "campaign_households", promo_shift)

    # --- fitted identity -----------------------------------------------------
    df["dept_volatility"] = df.department.map(stats.volatility)
    df["dept_persistence"] = df.department.map(stats.persistence)
    df["dept_log_scale"] = df.department.map(stats.log_scale)

    # --- target --------------------------------------------------------------
    df["target_ratio"] = df.revenue / scale

    df["split"] = df.week_no.map(C.split_of)
    df = df[df.week_no >= C.FIRST_TARGET_WEEK].copy()

    keep = (["department", "week_no", "split", "revenue", "scale_usd",
             "target_ratio"] + list(C.FEATURE_NAMES))
    out = df[keep].reset_index(drop=True)

    missing = [c for c in C.FEATURE_NAMES if out[c].isna().any()]
    if missing:
        raise ValueError(
            f"features contain NaN after the first target week: {missing}. "
            "Every window is sized so it is complete by then, so a NaN here "
            "means a window reached below the history floor.")
    return out


def feature_matrix(frame: pd.DataFrame) -> np.ndarray:
    """Columns in contract order. Never rely on frame column order."""
    return frame[list(C.FEATURE_NAMES)].to_numpy(dtype=float)
