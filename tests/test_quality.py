"""Tests for the data quality suite.

Two things are tested without needing a loaded database: that every check is
well-formed, and that the pass/fail logic is correct. The suite's value is that
it fails when it should, so the failure paths are tested explicitly rather than
assumed from a green run.
"""

from __future__ import annotations

import pytest

from rrip.quality.checks import CHECKS, Check
from rrip.quality.runner import evaluate


class FakeCursor:
    def __init__(self, value):
        self.value, self._q = value, None

    def execute(self, sql, params=None):
        self._q = sql

    def fetchone(self):
        # The recorded-value lookup is distinguishable by its parameterised SQL.
        if self._q and "etl_data_quality" in self._q:
            return (self.value,) if self.value is not None else None
        return (self.value,)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeConn:
    def __init__(self, observed, recorded=None):
        self.observed, self.recorded = observed, recorded

    def cursor(self):
        return FakeCursor(self.observed)


class RecordingConn(FakeConn):
    """Returns `observed` for the check query and `recorded` for the lookup."""

    def cursor(self):
        conn = self

        class C(FakeCursor):
            def __init__(self):
                super().__init__(None)

            def fetchone(self):
                if self._q and "etl_data_quality" in self._q:
                    return None if conn.recorded is None else (conn.recorded,)
                return (conn.observed,)

        return C()


# --- definitions are well-formed ------------------------------------------

def test_every_check_has_a_valid_rule() -> None:
    for c in CHECKS:
        assert c.rule in {"zero", "max", "min", "recorded"}, c.name


def test_threshold_rules_have_thresholds() -> None:
    for c in CHECKS:
        if c.rule in {"max", "min"}:
            assert c.threshold is not None, f"{c.name} needs a threshold"


def test_recorded_rules_name_a_key() -> None:
    for c in CHECKS:
        if c.rule == "recorded":
            assert c.recorded_key, f"{c.name} needs recorded_key"


def test_check_names_are_unique() -> None:
    names = [c.name for c in CHECKS]
    assert len(names) == len(set(names))


def test_severities_are_valid() -> None:
    for c in CHECKS:
        assert c.severity in {"error", "warn"}, c.name


def test_tolerance_checks_explain_themselves() -> None:
    """A threshold that is a judgement call must say so."""
    for c in CHECKS:
        if c.rule in {"max", "min"}:
            assert c.rationale, f"{c.name} has a threshold but no rationale"


def test_no_float32_anomaly_values_hardcoded() -> None:
    """The Phase 0 profile computed money in float32 and got 36 and 17.

    The NUMERIC values are 10 and 1. If those float32 figures ever appear as
    thresholds here, a floating-point artefact has become an expectation.
    """
    for c in CHECKS:
        if c.category == "anomalies" and c.threshold is not None:
            assert c.threshold not in (36, 17, 18850), (
                f"{c.name} uses a float32-derived value")


# --- pass/fail logic -------------------------------------------------------

def test_zero_rule_passes_on_zero() -> None:
    chk = Check("t", "c", "SELECT 0", "zero")
    assert evaluate(FakeConn(0), chk)["passed"] is True


def test_zero_rule_fails_on_nonzero() -> None:
    chk = Check("t", "c", "SELECT 1", "zero")
    r = evaluate(FakeConn(5), chk)
    assert r["passed"] is False
    assert "got 5" in r["detail"]


def test_max_rule_boundary() -> None:
    chk = Check("t", "c", "SELECT x", "max", threshold=1.0)
    assert evaluate(FakeConn(1.0), chk)["passed"] is True
    assert evaluate(FakeConn(1.01), chk)["passed"] is False


def test_min_rule_boundary() -> None:
    chk = Check("t", "c", "SELECT x", "min", threshold=801)
    assert evaluate(FakeConn(801), chk)["passed"] is True
    assert evaluate(FakeConn(800), chk)["passed"] is False


def test_recorded_rule_matches() -> None:
    chk = Check("t", "c", "SELECT x", "recorded", recorded_key="k")
    assert evaluate(RecordingConn(14466, recorded=14466), chk)["passed"] is True


def test_recorded_rule_detects_drift() -> None:
    chk = Check("t", "c", "SELECT x", "recorded", recorded_key="k")
    assert evaluate(RecordingConn(14467, recorded=14466), chk)["passed"] is False


def test_recorded_rule_fails_when_nothing_recorded() -> None:
    """Missing a recorded value must fail, not silently pass.

    Otherwise a suite run against a database that was never loaded would report
    green, which is worse than reporting an error.
    """
    chk = Check("t", "c", "SELECT x", "recorded", recorded_key="k")
    r = evaluate(RecordingConn(14466, recorded=None), chk)
    assert r["passed"] is False
    assert "no recorded value" in r["detail"]


def test_unknown_rule_raises() -> None:
    chk = Check("t", "c", "SELECT x", "nonsense")
    with pytest.raises(ValueError):
        evaluate(FakeConn(0), chk)


# --- --sql-only, the mode CI runs ----------------------------------------------

class _AbortingConn:
    """Mimics Postgres: after one failed statement the transaction is aborted
    and every later statement fails too, until rollback()."""

    def __init__(self):
        self.aborted, self.rollbacks = False, 0

    def cursor(self):
        conn = self

        class _Cur:
            def execute(self, sql, params=None):
                if conn.aborted:
                    raise RuntimeError("InFailedSqlTransaction")
                if "BROKEN" in sql:
                    conn.aborted = True
                    raise RuntimeError("syntax error")

            def fetchone(self):
                return (5,)          # a threshold miss for a 'zero' rule

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        return _Cur()

    def rollback(self):
        self.aborted, self.rollbacks = False, self.rollbacks + 1


def _run_with(monkeypatch, tmp_path, checks, sql_only):
    from contextlib import contextmanager

    import rrip.quality.runner as R

    conn = _AbortingConn()

    @contextmanager
    def fake_connect():
        yield conn

    monkeypatch.setattr(R, "connect", fake_connect)
    monkeypatch.setattr(R, "CHECKS", checks)
    failures, warnings, path = R.run(report_dir=tmp_path, sql_only=sql_only)
    return failures, conn


def test_sql_only_ignores_threshold_misses(monkeypatch, tmp_path) -> None:
    checks = [Check("miss", "c", "SELECT 5", "zero")]
    assert _run_with(monkeypatch, tmp_path, checks, sql_only=True)[0] == 0
    assert _run_with(monkeypatch, tmp_path, checks, sql_only=False)[0] == 1


def test_sql_only_fails_on_sql_that_raises(monkeypatch, tmp_path) -> None:
    checks = [Check("broken", "c", "SELECT BROKEN", "zero")]
    assert _run_with(monkeypatch, tmp_path, checks, sql_only=True)[0] == 1


def test_one_raising_check_does_not_poison_the_rest(monkeypatch, tmp_path) -> None:
    # Without the rollback, 'after' would raise InFailedSqlTransaction and be
    # counted as broken SQL too.
    checks = [Check("broken", "c", "SELECT BROKEN", "zero"),
              Check("after", "c", "SELECT 5", "zero")]
    failures, conn = _run_with(monkeypatch, tmp_path, checks, sql_only=True)
    assert failures == 1
    assert conn.rollbacks == 1
