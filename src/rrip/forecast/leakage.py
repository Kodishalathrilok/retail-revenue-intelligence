"""The leakage audit: automated checks that no feature saw the future.

This project already treats evaluation as infrastructure rather than a
write-up, and applies the same rule here -- every leak found becomes a
permanent check, and the checks run as part of training rather than as a
notebook someone remembers to open.

Six checks, each targeting a specific way this pipeline could leak:

  1. DECLARED CUTOFF     no registered feature declares an offset after the
                         prediction cutoff.
  2. HISTORY FLOOR       no feature window reaches below week 20 for the
                         earliest target, so the panel-recruitment ramp cannot
                         enter a rolling statistic.
  3. TARGET INDEPENDENCE the empirical check. Every feature is recomputed on a
                         panel whose target week has been corrupted; any
                         feature that moves read the target.
  4. FUTURE WINDOW       rolling statistics are recomputed with the target week
                         forcibly excluded and must be bit-identical.
  5. FITTED SCOPE        department statistics and winsorisation bounds are
                         fitted on training rows only.
  6. SPLIT ORDER         train < validation < test, no overlap, no gaps.

And one experiment, which is the part that makes the rest mean something:

  ORACLE COMPARISON      a variant that deliberately reads the target week's
                         promotion data is trained and scored. If the strict
                         model matched it, the guard would be protecting
                         nothing and the checks above would be theatre. The
                         measured gap is what shows the boundary is load-bearing.

WHY CHECK 3 IS THE ONE THAT MATTERS

Checks 1, 2, 5 and 6 read declarations and constants -- they verify that the
code says the right thing. Check 3 does not trust any declaration: it changes
the answer and confirms the questions did not change. A feature that leaks
through a path nobody registered still fails it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from rrip.forecast import contract as C
from rrip.forecast import features as F


@dataclass
class Check:
    name: str
    passed: bool
    detail: str
    evidence: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"name": self.name, "passed": self.passed,
                "detail": self.detail, "evidence": self.evidence}


class LeakageError(AssertionError):
    pass


def check_declared_cutoff() -> Check:
    bad = [f.name for f in C.FEATURES if f.max_week_offset > 0]
    return Check(
        "declared_cutoff", not bad,
        ("no registered feature reads a week after the prediction cutoff"
         if not bad else
         f"features declare an offset after the cutoff: {bad}"),
        {"n_features": len(C.FEATURES), "violations": bad})


def check_history_floor() -> Check:
    """The deepest window on the earliest target must not predate week 20."""
    worst = min(f.min_week_offset for f in C.FEATURES)
    # For target week w the cutoff is w-1, so the oldest week read is
    # (w - 1) + min_offset.
    oldest = (C.FIRST_TARGET_WEEK - 1) + worst
    ok = oldest >= C.HISTORY_FLOOR_WEEK
    return Check(
        "history_floor", ok,
        (f"earliest target week {C.FIRST_TARGET_WEEK} reads back to week "
         f"{oldest}, at or after the floor {C.HISTORY_FLOOR_WEEK}"
         if ok else
         f"earliest target week {C.FIRST_TARGET_WEEK} reads back to week "
         f"{oldest}, which is inside the panel-recruitment ramp"),
        {"deepest_offset": worst, "oldest_week_read": oldest,
         "history_floor": C.HISTORY_FLOOR_WEEK})


def check_target_independence(panel: pd.DataFrame,
                              stats: F.FittedStats) -> Check:
    """Corrupt each target week's revenue; no feature on that row may move.

    The strongest check available without a second implementation. It builds
    the features twice -- once normally, once from a panel where every
    department's revenue has been multiplied by 1000 in a chosen week -- and
    asserts that the feature rows whose TARGET is that week are unchanged.

    A feature that reads its own target week will change. So will a rolling
    window that includes it. Nothing else can.
    """
    baseline = F.build_features(panel, stats)

    moved: dict[str, list[str]] = {}
    probes = [C.FIRST_TARGET_WEEK + 5, 60, C.LAST_TARGET_WEEK]
    for probe_week in probes:
        corrupted = panel.copy()
        mask = corrupted.week_no == probe_week
        corrupted.loc[mask, "revenue"] = corrupted.loc[mask, "revenue"] * 1000 + 7777
        corrupted.loc[mask, "panel_revenue"] = (
            corrupted.loc[mask, "panel_revenue"] * 1000 + 7777)
        corrupted.loc[mask, "on_display"] = corrupted.loc[mask, "promo_rows"]
        corrupted.loc[mask, "in_mailer"] = 0
        corrupted.loc[mask, "campaigns_active"] = 99

        after = F.build_features(corrupted, stats)
        a = baseline[baseline.week_no == probe_week].reset_index(drop=True)
        b = after[after.week_no == probe_week].reset_index(drop=True)

        for name in C.FEATURE_NAMES:
            if not np.allclose(a[name].to_numpy(float), b[name].to_numpy(float),
                               rtol=1e-12, atol=1e-12, equal_nan=True):
                moved.setdefault(name, []).append(str(probe_week))

    ok = not moved
    return Check(
        "target_independence", ok,
        ("corrupting a week's revenue, promotions and campaigns changed no "
         "feature on the row whose target is that week"
         if ok else
         f"features changed when their own target week was corrupted: "
         f"{sorted(moved)}"),
        {"probe_weeks": probes, "features_that_moved": moved})


def check_future_window(panel: pd.DataFrame, stats: F.FittedStats) -> Check:
    """Rolling features must be identical when built from a truncated panel.

    Take the features for target week w computed from the full panel, and
    recompute them from a panel that ENDS at week w-1. If any rolling window
    silently included weeks at or after w, the two disagree.
    """
    probe = 70
    full = F.build_features(panel, stats)
    truncated_panel = panel[panel.week_no <= probe - 1]
    # Re-add the target week with no actuals so the row exists to compare.
    from rrip.forecast.panel import extend_for_forecast
    truncated = F.build_features(extend_for_forecast(truncated_panel, probe), stats)

    a = full[full.week_no == probe].sort_values("department").reset_index(drop=True)
    b = truncated[truncated.week_no == probe].sort_values(
        "department").reset_index(drop=True)

    diffs = []
    for name in C.FEATURE_NAMES:
        if not np.allclose(a[name].to_numpy(float), b[name].to_numpy(float),
                           rtol=1e-10, atol=1e-10, equal_nan=True):
            diffs.append(name)

    ok = not diffs
    return Check(
        "future_window", ok,
        (f"features for target week {probe} are identical whether built from "
         "the full panel or from a panel truncated at the cutoff"
         if ok else
         f"features differ when the panel is truncated at the cutoff: {diffs}"),
        {"probe_week": probe, "differing_features": diffs})


def check_fitted_scope(stats: F.FittedStats) -> Check:
    """Department statistics must be fitted on training weeks only."""
    ok = tuple(stats.fitted_on_weeks) == tuple(C.TRAIN_WEEKS)
    return Check(
        "fitted_scope", ok,
        (f"department statistics fitted on weeks {stats.fitted_on_weeks}, "
         "which is the training window"
         if ok else
         f"department statistics fitted on {stats.fitted_on_weeks}, but the "
         f"training window is {C.TRAIN_WEEKS}. Encoding a department's "
         "volatility using validation or test weeks leaks without any single "
         "feature referencing the future."),
        {"fitted_on": list(stats.fitted_on_weeks),
         "train_window": list(C.TRAIN_WEEKS)})


def check_split_order() -> Check:
    tr, va, te = C.TRAIN_WEEKS, C.VALIDATION_WEEKS, C.TEST_WEEKS
    problems = []
    if tr[1] >= va[0]:
        problems.append(f"train ends {tr[1]} but validation starts {va[0]}")
    if va[1] >= te[0]:
        problems.append(f"validation ends {va[1]} but test starts {te[0]}")
    if va[0] != tr[1] + 1 or te[0] != va[1] + 1:
        problems.append("splits are not contiguous, so weeks are silently unused")
    for f in __import__("rrip.forecast.split", fromlist=["x"]).rolling_origin_folds():
        if f.validate_weeks[1] >= te[0]:
            problems.append(f"{f.describe()} reaches into the test window")

    ok = not problems
    return Check(
        "split_order", ok,
        ("train, validation and test are contiguous, ordered and non-"
         "overlapping, and no rolling-origin fold reaches the test window"
         if ok else "; ".join(problems)),
        {"train": list(tr), "validation": list(va), "test": list(te)})


def run_audit(panel: pd.DataFrame, stats: F.FittedStats,
              strict: bool = True) -> dict:
    """Run every check. Raises on failure when `strict`."""
    checks = [
        check_declared_cutoff(),
        check_history_floor(),
        check_split_order(),
        check_fitted_scope(stats),
        check_target_independence(panel, stats),
        check_future_window(panel, stats),
    ]
    failed = [c for c in checks if not c.passed]
    if failed and strict:
        raise LeakageError(
            "leakage audit failed:\n" +
            "\n".join(f"  - {c.name}: {c.detail}" for c in failed))
    return {"passed": not failed, "n_checks": len(checks),
            "checks": [c.to_dict() for c in checks]}


def oracle_comparison(panel: pd.DataFrame, stats: F.FittedStats,
                      make_model, folds=None) -> dict:
    """Train the same model with next week's promotions visible, and compare.

    This is the experiment that gives the audit meaning. `allow_future_promo`
    shifts the promotion and campaign features forward by one week, so they
    describe the week being predicted -- exactly the leak a careless
    implementation introduces, and exactly the feature a real retailer with a
    locked mailer plan would legitimately have.

    Both variants are scored on the same rolling-origin folds. A gap means the
    boundary is doing work; no gap would mean the promotion features carry
    nothing at this grain, which is also worth knowing and is reported either
    way.

    The oracle model is never saved and never served.
    """
    from rrip.forecast import baselines as B
    from rrip.forecast import metrics as M
    from rrip.forecast import models as MD
    from rrip.forecast import split as S

    folds = folds or S.rolling_origin_folds()

    def cv(frame: pd.DataFrame) -> M.Scores:
        acts, preds = [], []
        for _, trf, vaf in S.iter_folds(frame, folds):
            model = make_model()
            model.fit(F.feature_matrix(trf), trf.target_ratio.to_numpy(float),
                      B.clipped_scale(trf))
            preds.append(MD.to_dollars(model.predict(F.feature_matrix(vaf)),
                                       B.clipped_scale(vaf)))
            acts.append(vaf.revenue.to_numpy(float))
        return M.score(np.concatenate(acts), np.concatenate(preds))

    strict = cv(F.build_features(panel, stats, allow_future_promo=False))
    oracle = cv(F.build_features(panel, stats, allow_future_promo=True))

    delta = strict.wape - oracle.wape
    return {
        "strict": strict.to_dict(),
        "oracle": oracle.to_dict(),
        "wape_advantage_of_leaking": round(delta, 4),
        "relative_advantage_pct": round(100.0 * delta / strict.wape, 2),
        "interpretation": (
            "The oracle variant reads promotion and campaign activity in the "
            "week it is predicting. It is not servable and is never saved. The "
            "gap is what the strict cutoff costs -- and what a leaking "
            "implementation would have reported as model quality."),
    }
