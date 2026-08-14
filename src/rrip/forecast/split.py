"""Chronological splitting, and a lock on the test set.

The test range is not merely documented as untouched -- reaching it requires
calling test_frame() with an explicit unlock token. Selection code has no
reason to hold that token, so an accidental peek is an import error rather than
a quietly optimistic number in the release report.

That is deliberately more friction than a comment. The failure this prevents
does not announce itself: a hyperparameter chosen because it scored well on
test produces a test score that is no longer an estimate of anything, and
nothing in the output looks different.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import pandas as pd

from rrip.forecast import contract as C

# Passed to test_frame() to state that model and hyperparameter selection are
# finished. It carries no security weight; it is a speed bump with a name that
# shows up in a diff.
TEST_UNLOCK = "selection-frozen"


class TestSetLocked(RuntimeError):
    pass


@dataclass(frozen=True)
class Fold:
    """One rolling-origin fold: fit on everything up to `origin`, predict after."""

    index: int
    train_weeks: tuple[int, int]
    validate_weeks: tuple[int, int]

    def describe(self) -> str:
        return (f"fold {self.index}: train {self.train_weeks[0]}-"
                f"{self.train_weeks[1]} -> validate {self.validate_weeks[0]}-"
                f"{self.validate_weeks[1]}")


def train_frame(frame: pd.DataFrame) -> pd.DataFrame:
    lo, hi = C.TRAIN_WEEKS
    return frame[(frame.week_no >= lo) & (frame.week_no <= hi)]


def validation_frame(frame: pd.DataFrame) -> pd.DataFrame:
    lo, hi = C.VALIDATION_WEEKS
    return frame[(frame.week_no >= lo) & (frame.week_no <= hi)]


def test_frame(frame: pd.DataFrame, unlock: str = "") -> pd.DataFrame:
    """The held-out test weeks. Requires TEST_UNLOCK.

    Every caller of this function is a place where a final number is produced.
    There should be very few of them, and they should be easy to find.
    """
    if unlock != TEST_UNLOCK:
        raise TestSetLocked(
            "the test weeks are locked during model and hyperparameter "
            f"selection. Pass unlock={TEST_UNLOCK!r} only from code that runs "
            "after selection is frozen -- if you are tuning, use "
            "rolling_origin_folds() on train+validation instead.")
    lo, hi = C.TEST_WEEKS
    return frame[(frame.week_no >= lo) & (frame.week_no <= hi)]


def rolling_origin_folds(n_origins: int = C.N_ORIGINS,
                         step: int = C.ORIGIN_STEP) -> list[Fold]:
    """Expanding-window folds across TRAIN and VALIDATION.

    Expanding rather than sliding: a supermarket refitting weekly keeps its
    history, so discarding the early weeks would evaluate a process nobody
    runs. Each fold's validation block sits strictly after its training block,
    so every fold is a genuine one-week-ahead forecast repeated `step` times.

    The last fold's validation block ends at the last validation week, and no
    fold reaches into TEST.
    """
    end = C.VALIDATION_WEEKS[1]
    start = C.TRAIN_WEEKS[0]
    folds: list[Fold] = []
    for i in range(n_origins):
        v_hi = end - step * (n_origins - 1 - i)
        v_lo = v_hi - step + 1
        t_hi = v_lo - 1
        if t_hi - start + 1 < step * 2:
            raise ValueError(
                f"fold {i} would train on fewer than {step * 2} weeks "
                f"({start}-{t_hi}); reduce n_origins or step")
        folds.append(Fold(i, (start, t_hi), (v_lo, v_hi)))
    return folds


def iter_folds(frame: pd.DataFrame,
               folds: list[Fold] | None = None
               ) -> Iterator[tuple[Fold, pd.DataFrame, pd.DataFrame]]:
    for fold in folds or rolling_origin_folds():
        tl, th = fold.train_weeks
        vl, vh = fold.validate_weeks
        tr = frame[(frame.week_no >= tl) & (frame.week_no <= th)]
        va = frame[(frame.week_no >= vl) & (frame.week_no <= vh)]
        yield fold, tr, va


def describe_splits(frame: pd.DataFrame) -> dict:
    """Split sizes, for the report and the artifact metadata."""
    def block(name: str, window: tuple[int, int]) -> dict:
        lo, hi = window
        sub = frame[(frame.week_no >= lo) & (frame.week_no <= hi)]
        return {"split": name, "weeks": [lo, hi], "n_weeks": hi - lo + 1,
                "rows": int(len(sub)),
                "departments": int(sub.department.nunique()) if len(sub) else 0,
                "revenue_total": round(float(sub.revenue.sum()), 2)}

    return {
        "method": "chronological, no shuffling",
        "blocks": [block("train", C.TRAIN_WEEKS),
                   block("validation", C.VALIDATION_WEEKS),
                   block("test", C.TEST_WEEKS)],
        "rolling_origin_folds": [f.describe() for f in rolling_origin_folds()],
        "why_not_random": (
            "Rolling features for target week w average weeks w-8..w-1, so a "
            "random split places a test row's target inside a training row's "
            "feature window. The inclusion rule and the normalisation scale "
            "are also fitted on training weeks, and under a random split those "
            "would be fitted on data surrounding the test weeks. A random "
            "split would additionally measure interpolation between known "
            "weeks, which is not the task a planner faces."),
    }
