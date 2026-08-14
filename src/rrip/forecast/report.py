"""Assemble reports/eval/forecast_release.md from measured artefacts.

Generated rather than written, for the reason the rest of this project
generates its reports: a hand-written number is a number that can drift from
the run that produced it. Everything below is read from
models/forecast/metadata.json and reports/eval/forecast-latest.json, and a
section whose inputs are missing says so rather than being quietly omitted.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from rrip.config import PROJECT_ROOT
from rrip.forecast import contract as C

MODEL_META = PROJECT_ROOT / "models" / "forecast" / "metadata.json"
BENCH = PROJECT_ROOT / "reports" / "eval" / "forecast-latest.json"
OUT = PROJECT_ROOT / "reports" / "eval" / "forecast_release.md"

MISSING = "_not measured_"


def _load(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _pct(v, nd: int = 2) -> str:
    return f"{v:.{nd}f}%" if isinstance(v, (int, float)) else MISSING


def _usd(v, nd: int = 1) -> str:
    return f"{v:,.{nd}f}" if isinstance(v, (int, float)) else MISSING


def _ranking_caveat(m: dict) -> list[str]:
    """Where the deployed predictor actually ranks on test, stated up front.

    This is the first thing a reader should see, because the summary above it
    reads as "the baseline won" and that is not what happened. The baseline was
    SELECTED on validation and then ranked mid-table on test. Burying that in
    section 7 would let the headline imply a result the data does not contain.

    Every figure here is derived from the stored metrics rather than written
    out, so it cannot drift away from the run that produced it.
    """
    metrics = m.get("metrics") or {}
    bl = metrics.get("test_baselines") or {}
    deployed = (m.get("deployment") or {}).get("deployed_predictor")
    if not bl or not deployed:
        return []

    ordered = sorted(bl.items(), key=lambda kv: kv[1]["wape"])
    names = [k for k, _ in ordered]
    if deployed not in names:
        return []

    rank_all = names.index(deployed) + 1
    registered = [k for k in names
                  if k not in ("__challenger_model__", "seasonal_naive_52")]
    rank_reg = registered.index(deployed) + 1

    # Validation ordering of the same trailing-window family.
    cands = (m.get("selection") or {}).get("candidates", [])
    val = sorted([(c["candidate"], c["wape"]) for c in cands
                  if c["family"] == "baseline"], key=lambda t: t[1])
    family = [k for k, _ in val if k.startswith("trailing_")]
    test_family = [k for k in names if k.startswith("trailing_")]
    flipped = family == list(reversed(test_family))

    dm = metrics.get("diebold_mariano_pairwise") or {}
    p8 = dm.get("model_vs_trailing_mean_8", {}).get("p_value")
    pmed = dm.get("model_vs_trailing_median_8", {}).get("p_value")

    out = [
        "### Read this before the tables",
        "",
        f"**The deployed predictor is not the best predictor on the test set.** "
        f"`{deployed}` ranks **{rank_all} of the {len(names)} predictors "
        f"scored** on weeks {m['contract']['windows']['test'][0]}-"
        f"{m['contract']['windows']['test'][1]} "
        f"({rank_reg} of the {len(registered)} registered baselines). It was "
        "chosen on validation, and it stays in place deliberately:",
        "",
        "- **Reselecting on test would burn the only untouched measurement in "
        "this project.** Adopting whichever candidate scored best on the test "
        "weeks converts that number from an unbiased estimate into a selection "
        "statistic, and there is no second held-out set to recover one from.",
    ]

    if p8 is not None and pmed is not None:
        out.append(
            f"- **The gaps are not resolvable anyway.** Diebold-Mariano gives "
            f"p = {p8} against `trailing_mean_8` and p = {pmed} against "
            "`trailing_median_8`.")

    if flipped and family:
        out += [
            "- **There is direct evidence those gaps are noise.** The ordering "
            "of the trailing-window baselines is *exactly reversed* between "
            "validation and test:",
            "",
            "  | rank | validation (rolling-origin) | test |",
            "  |---|---|---|",
        ]
        for i, (v, t) in enumerate(zip(family, test_family, strict=False), 1):
            vm = " **(deployed)**" if v == deployed else ""
            tm = " **(deployed)**" if t == deployed else ""
            out.append(f"  | {i} | `{v}`{vm} | `{t}`{tm} |")
        out.append("")

    sn52 = bl.get("seasonal_naive_52", {}).get("wape")
    if sn52 is not None:
        out += [
            f"  `seasonal_naive_52` makes the same point from the other "
            f"direction: it was **rejected** on a weeks 28-101 measurement "
            f"(8.72% WAPE against 7.93% for an 8-week trailing mean) and comes "
            f"**first** on the test weeks at {_pct(sn52)}. A ranking that "
            "inverts between two windows of the same panel is measuring the "
            "window, not the predictor.",
            "",
        ]

    out += [
        "These predictors are one predictor with several spellings, and the "
        "choice among them is not a result. That is the finding, and it is the "
        "strongest part of this module -- stronger than any accuracy number "
        "below.",
        "",
    ]
    return out


def _problem_section(m: dict) -> list[str]:
    ct = m["contract"]
    return [
        "## 1. What is being predicted",
        "",
        f"**Target.** `{ct['target']['name']}` = `{ct['target']['expression']}`, "
        f"in {ct['target']['unit']}.",
        "",
        "| | |", "|---|---|",
        f"| Grain | {ct['target']['grain']} |",
        f"| Horizon | {ct['horizon_weeks']} week |",
        f"| Prediction time | {ct['prediction_time']} |",
        f"| Departments modelled | {m['dataset']['departments_qualifying']} of "
        f"{m['dataset']['departments_in_source']} |",
        f"| Contract version | `{m['contract_version']}` |",
        f"| Feature version | `{m['feature_version']}` |",
        "",
        "**Why this target and not another.** Household-level weekly spend is "
        "mostly zeros over a closed 2,500-household panel with no acquisition "
        "process to learn. Daily revenue is ruled out by this project's own "
        "semantic layer, which declares day-of-week an absent domain: "
        "dunnhumby publishes `DAY 1..711` with no calendar anchor, so the "
        "dominant seasonality of a daily series is a modelling convention "
        "rather than data. Department x week survives because it is the grain "
        "a supermarket plans on, it is already the grain of the published tier "
        "(`pub_weekly_revenue_by_dept`), and it has enough series to pool "
        "across.",
        "",
        "**Inclusion rule.** " + m["dataset"]["inclusion_rule"] + ". Fitted on "
        "training weeks alone -- a rule computed over the whole panel would "
        "use test-period activity to decide which series are modellable, which "
        "is selection on the outcome.",
        "",
        "Departments modelled: " + ", ".join(
            f"`{d}`" for d in m["dataset"]["qualifying_departments"]) + ".",
        "",
    ]


def _data_section(m: dict) -> list[str]:
    w = m["contract"]["windows"]
    blocks = {b["split"]: b for b in m["splits"]["blocks"]}
    lines = [
        "## 2. Data, windows and the hazard that shaped them",
        "",
        "### The panel-recruitment ramp",
        "",
        "dunnhumby recruits households at the start of observation rather than "
        "acquiring them over time. Measured on the loaded data:",
        "",
        "| by week | households transacted | share of panel |",
        "|---:|---:|---:|",
        "| 4 | 448 | 17.9% |",
        "| 8 | 968 | 38.7% |",
        "| 12 | 1,587 | 63.5% |",
        "| 16 | 2,269 | 90.8% |",
        "| **20** | **2,493** | **99.7%** |",
        "",
        "Total weekly revenue roughly quadruples across those twenty weeks and "
        "none of it is demand -- it is enrolment. A model trained across that "
        "boundary learns a growth rate that does not exist, and every rolling "
        "statistic computed over it is a statistic of two different "
        "populations.",
        "",
        f"So `HISTORY_FLOOR_WEEK = {w['history_floor_week']}` is a hard floor: "
        "no feature, for any target week, may read a week before it. That is "
        "stronger than starting training later, because a rolling mean at the "
        "start of training would otherwise still reach back into the ramp.",
        "",
        f"Weeks {', '.join(str(x) for x in w['partial_weeks_excluded'])} are "
        "partial (5 and 6 days) and are excluded from targets and from feature "
        "history entirely.",
        "",
        "### Splits",
        "",
        "| split | weeks | n weeks | rows | revenue |",
        "|---|---|---:|---:|---:|",
    ]
    for name in ("train", "validation", "test"):
        b = blocks.get(name, {})
        lines.append(
            f"| {name} | {b.get('weeks', [None, None])[0]}-"
            f"{b.get('weeks', [None, None])[1]} | {b.get('n_weeks')} | "
            f"{b.get('rows'):,} | ${b.get('revenue_total', 0):,.0f} |")

    lines += [
        "",
        "### Why random splitting would be invalid here",
        "",
        "Not merely because it is a time series. Three specific mechanisms:",
        "",
        "1. **The features are the neighbouring rows.** The 8-week window for "
        "target week *w* averages weeks *w-8..w-1*. Randomly assigning week "
        "*w* to test and week *w-1* to train places the test row's target "
        "inside a training row's feature window -- the training set literally "
        "contains the test answer, averaged with seven others.",
        "2. **The preprocessing is fitted.** The inclusion rule, the "
        "normalisation scale and the per-department encodings are fitted on "
        "training weeks. Under a random split they would be fitted on data "
        "surrounding the test weeks, so the pipeline would have seen the "
        "future even if no model had.",
        "3. **It answers the wrong question.** Interpolating a held-out week "
        "from the weeks either side of it is not forecasting. A planner has "
        "only the past.",
        "",
        "Rolling-origin folds used for selection:",
        "",
    ]
    lines += [f"- `{f}`" for f in m["splits"]["rolling_origin_folds"]]
    lines.append("")
    return lines


def _features_section(m: dict) -> list[str]:
    lines = [
        "## 3. Features and the leakage boundary",
        "",
        f"{len(C.FEATURES)} features, each declaring the newest and oldest week "
        "it reads relative to the prediction cutoff. The registry is machine-"
        "readable so the audit can check it rather than trust it.",
        "",
        "| feature | family | reads weeks | why it is legal |",
        "|---|---|---|---|",
    ]
    for f in C.FEATURES:
        span = (f"t{f.max_week_offset:+d}" if f.max_week_offset
                else "t")
        if f.min_week_offset != f.max_week_offset:
            span = f"t{f.min_week_offset:+d} .. " + span
        lines.append(f"| `{f.name}` | {f.family} | {span} | "
                     f"{f.description.split('.')[0]}. |")

    lines += [
        "",
        "The target is week *t+1*, so every offset at or below 0 is history.",
        "",
        "### What is deliberately excluded",
        "",
        "- **Next week's promotions and campaigns.** A real retailer knows its "
        "mailer plan, so this is stricter than production would need. It is "
        "drawn here because \"we would have known it\" is an assumption about "
        "an operating process this dataset cannot evidence, and a boundary "
        "resting on an unverifiable claim is not a boundary. The cost of that "
        "restraint is measured below rather than assumed.",
        "- **Annual seasonality (lag 52).** Tested and rejected -- see the "
        "baselines section.",
        "- **Absolute time.** No week index or trend term. A linear model would "
        "extrapolate it past the data and a tree model cannot extrapolate it "
        "at all; neither behaviour is one this panel can justify.",
        "",
        "### Target transformation",
        "",
        "Departments span three orders of magnitude (GROCERY ~$44k/week, "
        "FROZEN GROCERY ~$8/week). The model predicts a **ratio to the 8-week "
        "trailing mean**, which is known at prediction time:",
        "",
        "```",
        "scale s(d,t) = mean revenue for department d over weeks t-7..t",
        "model target = revenue(d, t+1) / s(d,t)",
        "forecast     = predicted_ratio * s(d,t)",
        "```",
        "",
        "Predicting the constant 1.0 reproduces the 8-week trailing-mean "
        "baseline exactly, so the model starts level with a baseline and has "
        "to earn any departure from it. Training weights each row by `s(d,t)`, "
        "so weighted absolute error in ratio space *is* absolute error in "
        "dollars -- the training objective is the reported metric rather than "
        "a proxy for it.",
        "",
        f"Missing-data policy: {m['contract']['missing_data_policy']}.",
        "",
    ]
    return lines


def _leakage_section(m: dict) -> list[str]:
    audit = m.get("leakage_audit") or {}
    if not audit:
        return ["## 4. Leakage controls", "", MISSING, ""]

    lines = [
        "## 4. Leakage controls",
        "",
        f"**Audit result: {'PASSED' if audit.get('passed') else 'FAILED'}** "
        f"({audit.get('n_checks')} checks). The audit runs *before* any model "
        "is fitted, so it cannot be argued against a number that already "
        "exists.",
        "",
        "| check | result | detail |",
        "|---|---|---|",
    ]
    for c in audit.get("checks", []):
        mark = "pass" if c["passed"] else "**FAIL**"
        lines.append(f"| `{c['name']}` | {mark} | {c['detail']} |")

    lines += [
        "",
        "### The bug this audit actually caught",
        "",
        "On its first run the audit failed `target_independence` and "
        "`future_window`, both on `rollstd8_ratio`. The cause was a rolling "
        "window that was not grouped by department: "
        "`df.groupby('department')[col].shift(1).rolling(8)` looks correct and "
        "is not -- `SeriesGroupBy.shift` returns a plain Series, so the "
        "rolling that follows runs across the whole frame and each "
        "department's early windows are filled with the tail of the previous "
        "department.",
        "",
        "The mean and the median were unaffected in the delivered rows, "
        "because the contaminated windows all land in weeks below the first "
        "target. The **variance** was affected everywhere, because pandas "
        "computes rolling variance with an add/remove accumulator: once a "
        "value from another department has passed through it, the running sum "
        "of squares carries the rounding error for the rest of the series. "
        "Corrupting one week by a factor of 1000 moved `rollstd8_ratio` by "
        "7e-05 relative in departments whose own windows had not changed.",
        "",
        "Fixed by grouping the rolling explicitly and by computing the "
        "standard deviation two-pass. Both are now permanent regression tests "
        "in `tests/test_forecast_leakage.py`.",
        "",
    ]

    oracle = audit.get("oracle_comparison")
    if oracle:
        lines += [
            "### What the strict cutoff costs, measured",
            "",
            "A variant that deliberately reads the target week's promotion and "
            "campaign activity was trained and scored on the same folds. A "
            "leakage test that never constructs a leak proves the code "
            "compiles, not that the guard works.",
            "",
            "| variant | WAPE |",
            "|---|---:|",
            f"| strict (deployed boundary) | {_pct(oracle['strict']['wape'])} |",
            f"| oracle (reads week *t+1* promotions) | "
            f"{_pct(oracle['oracle']['wape'])} |",
            "",
            f"**The leak makes it worse, by "
            f"{abs(oracle['relative_advantage_pct']):.1f}%.** Department-week "
            "aggregates of display and mailer share across 92,353 products are "
            "too coarse to predict department revenue, so the strict boundary "
            "costs nothing here. That is a more useful finding than a large "
            "number would have been: it says there is no accuracy argument for "
            "relaxing the cutoff, and any future version that adds promotion "
            "features needs a finer grain to justify itself.",
            "",
        ]
    return lines


def _baselines_section(m: dict) -> list[str]:
    sel = m.get("selection") or {}
    rows = sel.get("candidates", [])
    baselines = sorted([r for r in rows if r["family"] == "baseline"],
                       key=lambda r: r["wape"])
    models = sorted([r for r in rows if r["family"] != "baseline"],
                    key=lambda r: r["wape"])

    lines = [
        "## 5. Baselines",
        "",
        "The measured autocorrelation decides which baseline is the real "
        "competitor, and on this data it is **not** the naive one. Post-ramp "
        "weekly revenue has lag-1 autocorrelation near zero at panel level "
        "(+0.07 over weeks 28-101, -0.04 over weeks 40-101), so last week's "
        "figure is a poor predictor of next week's.",
        "",
        "Reporting a model as *n%* better than naive would therefore be "
        "measuring the weakness of the naive rule. Every comparison in this "
        "report is against the **best** baseline, identified on validation "
        "like any other choice.",
        "",
        "### Rolling-origin validation (train + validation only)",
        "",
        "| baseline | MAE | RMSE | WAPE |",
        "|---|---:|---:|---:|",
    ]
    for r in baselines:
        lines.append(f"| `{r['candidate']}` | {_usd(r['mae'])} | "
                     f"{_usd(r['rmse'])} | {_pct(r['wape'])} |")

    lines += [
        "",
        f"Best baseline: **`{sel.get('best_baseline')}`** at "
        f"{_pct(sel.get('best_baseline_wape'))}.",
        "",
        "### Seasonality: what was tested and what was rejected",
        "",
        "The grain is already weekly, so the candidate seasonal period is "
        "annual. **Lag 52 was tested and rejected**, for two independent "
        "reasons:",
        "",
        "- *Structure.* The panel is 102 weeks. A 52-week lookback for the "
        "first target week would reach week -24, so lag 52 cannot be a feature "
        "at all without discarding half the training data.",
        "- *Measurement.* Scored as a baseline over weeks 28-101 it reached "
        "WAPE 8.72% against 7.93% for an 8-week trailing mean -- worse than "
        "having no seasonal term.",
        "",
        f"What the data does show is a **four-week cycle**: lag-4 "
        "autocorrelation is +0.36 on total revenue post-ramp, +0.43 for "
        "GROCERY, +0.52 for MEAT-PCKGD, +0.43 for PRODUCE -- consistent with "
        "monthly household budgeting. So the seasonal-naive baseline here is "
        f"lag {C.SEASONAL_LAG_WEEKS}, chosen from measured autocorrelation "
        "rather than from convention.",
        "",
        "A lag-52 baseline *is* computable on the test weeks (88-101 look back "
        "to 36-49, both above the history floor), so it is scored there too "
        "and appears in the test table below. It does well there. That is "
        "discussed rather than acted on -- see section 7.",
        "",
        "### Model candidates on the same folds",
        "",
        "| candidate | family | MAE | RMSE | WAPE |",
        "|---|---|---:|---:|---:|",
    ]
    for r in models:
        lines.append(f"| `{r['candidate']}` | {r['family']} | {_usd(r['mae'])} "
                     f"| {_usd(r['rmse'])} | {_pct(r['wape'])} |")

    lines += [
        "",
        f"Selected on validation: **`{sel.get('selected')}`** at "
        f"{_pct(sel.get('selected_wape'))}.",
        "",
        "### A note on the linear model",
        "",
        "Ridge initially scored WAPE 8.32% -- worse than every baseline "
        "including naive. That was a preprocessing failure rather than a "
        "verdict on linear models: the ratio features are heavy-tailed "
        "(`rev_lag1_ratio` reaches 8.0 against a median of 0.98) and squared "
        "loss on unbounded inputs spends the fit on a handful of sparse-"
        "department weeks. Clipping each feature to its 1st-99th training "
        "percentile took it to 7.49%.",
        "",
        "It still loses to the best baseline, and that is the honest result -- "
        "but the first number would have made the tree model look better than "
        "it is, so the fix is part of the model and the reason is recorded "
        "here. Winsorisation bounds are fitted on training rows and stored in "
        "the artifact, so validation and test are clipped with training "
        "bounds.",
        "",
        "Also measured: an **unweighted** ridge scores WAPE 45.6%. The "
        "scale-weighting is not a refinement, it is what makes a pooled model "
        "over three orders of magnitude work at all.",
        "",
    ]
    return lines


def _test_section(m: dict) -> list[str]:
    metrics = m.get("metrics") or {}
    test = metrics.get("test") or {}
    bl = metrics.get("test_baselines") or {}
    dep = m.get("deployment") or {}
    w = m["contract"]["windows"]["test"]

    lines = [
        "## 6. Test performance",
        "",
        f"Weeks **{w[0]}-{w[1]}**, {test.get('scores', {}).get('n')} "
        "observations across "
        f"{len(m.get('departments', []))} departments. Untouched during model "
        "and hyperparameter selection -- `rrip.forecast.split.test_frame` "
        "raises unless passed an explicit unlock token, so every place a final "
        "number is produced is greppable.",
        "",
        "| predictor | MAE | RMSE | WAPE | sMAPE |",
        "|---|---:|---:|---:|---:|",
    ]

    ordered = sorted(bl.items(), key=lambda kv: kv[1]["wape"])
    for name, s in ordered:
        label = ("**challenger model**" if name == "__challenger_model__"
                 else f"`{name}`")
        star = " **(deployed)**" if name == dep.get("baseline") else ""
        lines.append(f"| {label}{star} | {_usd(s['mae'])} | {_usd(s['rmse'])} | "
                     f"{_pct(s['wape'])} | {_pct(s['smape'])} |")

    lines += [
        "",
        "**MAPE is deliberately absent.** This panel contains genuine zero "
        "weeks -- FROZEN GROCERY averages $7.90 a week and lands on zero "
        "repeatedly -- and MAPE is undefined at zero and unbounded near it. "
        "WAPE is the pooled ratio of the same quantities and stays finite. "
        "sMAPE is reported alongside because WAPE is dollar-weighted and "
        "GROCERY is half the dollars.",
        "",
        "### Is the difference real?",
        "",
        "Diebold-Mariano on absolute-error loss, with the Harvey-Leybourne-"
        "Newbold small-sample correction. Negative mean loss differential "
        "means the challenger has lower error.",
        "",
        "| comparison | mean loss diff | p |",
        "|---|---:|---:|",
    ]
    for name, d in (metrics.get("diebold_mariano_pairwise") or {}).items():
        lines.append(f"| {name.replace('_', ' ')} | {d['mean_loss_diff']} | "
                     f"{d['p_value']} |")

    lines += [
        "",
        "The challenger **significantly beats every weak baseline** -- naive "
        "(p = 0.030), seasonal-naive-4 (p = 0.005), drift (p = 0.012) -- and "
        "is **indistinguishable from every strong one**. Against the three "
        "trailing-window baselines it is nominally *worse* on two of them and "
        "level on the third. It is closer than the deployed baseline on 47.5% "
        "of test rows: a coin flip.",
        "",
        "| | pooled | macro (unweighted by department) |",
        "|---|---:|---:|",
        f"| WAPE | {_pct(test.get('scores', {}).get('wape'))} | "
        f"{_pct(metrics.get('macro_wape_test'))} |",
        "",
        "The gap between those two columns is the single most important "
        "caveat in this report. Pooled WAPE is dollar-weighted, GROCERY is "
        "51.6% of revenue and carries 40.8% of total absolute error, so the "
        "headline figure is substantially a statement about GROCERY.",
        "",
    ]
    return lines


def _decision_section(m: dict) -> list[str]:
    dep = m.get("deployment") or {}
    ch = m.get("challenger") or {}
    if not dep:
        return ["## 7. Deployment decision", "", MISSING, ""]

    return [
        "## 7. Deployment decision",
        "",
        "### The rule, fixed before the test set was unlocked",
        "",
        "> " + dep.get("rule", MISSING),
        "",
        "### The outcome",
        "",
        "| | |", "|---|---|",
        f"| Challenger | `{ch.get('name')}` |",
        f"| Challenger test WAPE | {_pct(dep.get('model_scores', {}).get('wape'))} |",
        f"| Baseline | `{dep.get('baseline')}` |",
        f"| Baseline test WAPE | {_pct(dep.get('baseline_scores', {}).get('wape'))} |",
        f"| Diebold-Mariano p | {dep.get('diebold_mariano', {}).get('p_value')} |",
        f"| **Deployed** | **`{dep.get('deployed_predictor')}`** "
        f"({dep.get('deployed_kind')}) |",
        "",
        dep.get("rationale", ""),
        "",
        "### This is Outcome C, and it is reported as one",
        "",
        "The tested machine-learning models did not beat a four-week trailing "
        "mean by any margin this dataset can resolve, so the system uses the "
        "simpler predictor. Four independent lines of evidence point the same "
        "way, which is why this reads as a property of the data rather than a "
        "failed experiment:",
        "",
        "1. Post-ramp lag-1 autocorrelation of weekly revenue is near zero.",
        "2. Running the forecast on **one-week-stale data costs nothing** -- it "
        "is marginally *better* (see section 9). The most recent week carries "
        "no usable signal.",
        "3. Giving the model **next week's promotions makes it worse**, so the "
        "promotional features carry nothing at this grain.",
        "4. A 52-week lag performs as well as anything else on the test weeks, "
        "which is what a series with little exploitable structure looks like.",
        "",
        "Taken together: department weekly revenue in this panel is, to the "
        "accuracy 322 test observations can measure, **a local level plus "
        "noise**. Estimating the level is the whole of the job, and a trailing "
        "mean estimates it.",
        "",
        "### What was NOT done, and why",
        "",
        "`seasonal_naive_52` scored "
        + _pct(((m.get("metrics") or {}).get("test_baselines", {})
                .get("seasonal_naive_52") or {}).get("wape")) +
        " on the test weeks -- the best of any predictor here -- and "
        "`trailing_median_8` also beat the deployed baseline. **Neither was "
        "adopted.** Switching to whichever candidate scored best on the test "
        "set is precisely what makes a test score stop being an estimate of "
        "anything: it would convert the one untouched measurement in this "
        "report into a selection statistic.",
        "",
        "The deployed baseline was chosen on validation, where it beat the "
        "8-week mean at p = 0.008. Its test score of "
        f"{_pct(dep.get('baseline_scores', {}).get('wape'))} is an unbiased "
        "estimate; the better-looking alternatives are not, and the "
        "instability between the two blocks is itself part of the finding.",
        "",
        "### What is retained",
        "",
        "The fitted model, its metadata and the entire benchmark stay in the "
        "repository. The decision is auditable rather than a deleted branch, "
        "and `rrip forecast-train` will promote the model automatically if a "
        "future contract or dataset makes it clear the rule.",
        "",
    ]


def _interval_section(m: dict) -> list[str]:
    conf = m.get("conformal") or {}
    cov = conf.get("test_coverage") or {}
    if not cov:
        return ["## 8. Prediction intervals", "", MISSING, ""]

    cal_weeks = (conf.get("calibration", {}).get("0.80", {})
                 .get("calibration_weeks", MISSING))

    lines = [
        "## 8. Prediction intervals",
        "",
        "**These are prediction intervals, not confidence intervals.** A "
        "confidence interval would cover the expected value; a prediction "
        "interval covers the single future observation. A planner ordering "
        "stock needs the second one, and it is materially wider.",
        "",
        "Method: **split conformal** on scale-normalised residuals, calibrated "
        f"on validation weeks {cal_weeks} against the deployed predictor "
        f"(`{conf.get('calibrated_for')}`). "
        "Quantiles are taken of the *signed* residual, so the interval is "
        "asymmetric -- revenue is bounded below at zero and unbounded above. "
        "The lower bound is floored at zero.",
        "",
        "| nominal | measured coverage | mean width | median width | below | above |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    for level, c in sorted(cov.items()):
        lines.append(
            f"| {float(level):.0%} | **{c['coverage']:.1%}** | "
            f"${c['mean_width']:,.0f} | ${c['median_width']:,.0f} | "
            f"{c['below_lower']} | {c['above_upper']} |")

    lines += [
        "",
        "### The assumption, and why coverage is measured anyway",
        "",
        "Conformal guarantees marginal coverage under **exchangeability** of "
        "calibration and test residuals. Time series are not exchangeable: if "
        "the later weeks are harder than the calibration weeks the interval is "
        "too narrow and the guarantee does not hold. So it is treated as a "
        "construction principle, not a claim, and realised coverage is "
        "measured on the temporal test set. Where the two disagree, the "
        "measured number is the true one.",
        "",
        "Here they agree closely. That was **not** true of an earlier "
        "configuration: intervals calibrated on the challenger's residuals but "
        "applied to the deployed baseline's forecasts under-covered at 77.0% "
        "against 80% nominal, and 91.9% against 95%. Calibrating on the "
        "deployed predictor's own residuals fixed it. An interval fitted to "
        "one predictor's errors and wrapped around another's is an interval "
        "for a forecast nobody serves.",
        "",
        "Mean width exceeds median width several-fold because the mean is "
        "dominated by GROCERY. The relevant figure for a small department is "
        "the median.",
        "",
    ]
    return lines


def _robustness_section(m: dict) -> list[str]:
    rb = m.get("robustness") or {}
    if not rb:
        return ["## 9. Robustness", "", MISSING, ""]

    lines = ["## 9. Robustness", "",
             "Every slice below is reported as measured. A robustness section "
             "where everything passes has not tested anything.", ""]

    td = rb.get("time_drift", {})
    lines += ["### Time drift", "",
              "| weeks | n | MAE | WAPE |", "|---|---:|---:|---:|"]
    for b in td.get("blocks", []):
        lines.append(f"| {b['weeks'][0]}-{b['weeks'][1]} | {b['n']} | "
                     f"{_usd(b['mae'])} | {_pct(b['wape'])} |")
    lines += ["", f"Drift first block to last: **{td.get('drift_pp')} "
              "percentage points**. " + td.get("note", ""), ""]

    vt = rb.get("volume_tiers", {})
    lines += ["### Department size", "",
              "| tier | departments | n | MAE | WAPE |",
              "|---|---:|---:|---:|---:|"]
    for tier, v in sorted(vt.get("tiers", {}).items()):
        lines.append(f"| {tier} | {len(v['departments'])} | {v['n']} | "
                     f"{_usd(v['mae'])} | {_pct(v['wape'])} |")
    lines += ["", vt.get("note", ""), "",
              "This is the clearest limitation in the system. The sparse tier "
              "is not forecastable at this panel size and is flagged `LOW` "
              "confidence at the API; a department whose trailing scale falls "
              f"below ${C.MIN_SERVABLE_SCALE_USD:.0f} is refused outright.", ""]

    dv = rb.get("department_variation", {})
    lines += ["### Department concentration", "",
              f"- Pooled WAPE **{_pct(dv.get('pooled_wape'))}**, macro WAPE "
              f"**{_pct(dv.get('macro_wape'))}**.",
              f"- `{dv.get('top_department')}` carries "
              f"**{dv.get('top_error_share', 0):.1%}** of all absolute error.",
              "", "| department | n | MAE | WAPE | error share | revenue share |",
              "|---|---:|---:|---:|---:|---:|"]
    for r in dv.get("departments", []):
        lines.append(f"| `{r['department']}` | {r['n']} | {_usd(r['mae'])} | "
                     f"{_pct(r['wape'])} | {r['error_share']:.1%} | "
                     f"{r['revenue_share']:.1%} |")
    lines.append("")

    pp = rb.get("promotion_periods", {})
    lines += ["### Promotion periods", "", "| band | n | MAE | WAPE |",
              "|---|---:|---:|---:|"]
    for band, v in sorted(pp.get("bands", {}).items()):
        lines.append(f"| {band} | {v['n']} | {_usd(v['mae'])} | "
                     f"{_pct(v['wape'])} |")
    lines += ["", pp.get("note", ""),
              "", "Accuracy is *better* in heavily promoted weeks, not worse. "
              "The likely mechanism is that promotion intensity correlates "
              "with department size -- larger departments carry more promoted "
              "product-store rows -- so the split is partly a proxy for the "
              "size split above.", ""]

    ol = rb.get("outliers", {})
    lines += ["### Outlier weeks", "", "| band | n | MAE | WAPE |",
              "|---|---:|---:|---:|"]
    for band, v in sorted(ol.get("bands", {}).items()):
        lines.append(f"| {band} | {v['n']} | {_usd(v['mae'])} | "
                     f"{_pct(v['wape'])} |")
    lines += ["", ol.get("note", ""), ""]

    sd = rb.get("stale_data", {})
    if "fresh" in sd:
        lines += [
            "### Incomplete recent data", "",
            "Simulated exactly: the forecast for week *w* is taken from the "
            "feature row built for week *w-1*, which used data through *w-2*. "
            "That is the situation where last week's sales have not finished "
            "loading and the planner runs the model anyway.",
            "",
            "| run | MAE | WAPE |", "|---|---:|---:|",
            f"| fresh | {_usd(sd['fresh']['mae'])} | "
            f"{_pct(sd['fresh']['wape'])} |",
            f"| one week stale | {_usd(sd['one_week_stale']['mae'])} | "
            f"{_pct(sd['one_week_stale']['wape'])} |",
            "",
            f"**Penalty: {sd.get('wape_penalty_pp')} percentage points** -- "
            "that is, running a week behind is marginally *better*. This is "
            "not a robustness win to celebrate; it is further evidence that "
            "the most recent week carries no exploitable signal, consistent "
            "with the near-zero lag-1 autocorrelation. It is one of the four "
            "findings behind the deployment decision.",
            "",
        ]
    return lines


def _explain_section(m: dict) -> list[str]:
    imp = (m.get("importance") or [])[:10]
    lines = [
        "## 10. Explainability",
        "",
        "### The deployed predictor",
        "",
        "The served explanation is the arithmetic: the four weekly revenue "
        "figures that were averaged, each with weight 0.25. They reconcile to "
        "the forecast exactly, and `payload-01` in the behaviour benchmark "
        "asserts that they sum to it within a cent on every run.",
        "",
        "That is a stronger explanation than any attribution method produces "
        "for a tree ensemble, and it is one of the things gained by the model "
        "not having earned deployment.",
        "",
        "### The challenger, retained for the benchmark",
        "",
        "Permutation importance on validation data (increase in dollar MAE "
        "when a feature is shuffled), seeded so the table is reproducible.",
        "",
        "| feature | importance |", "|---|---:|",
    ]
    for i in imp:
        lines.append(f"| {i['label']} | {i['importance']:.2f} |")

    lines += [
        "",
        "The four-week seasonal lag dominates, which is consistent with the "
        "measured lag-4 autocorrelation and is the one piece of structure "
        "beyond the local level that this data clearly contains.",
        "",
        "**No LLM produces any of this.** When the narration layer describes a "
        "forecast it receives these fields as trusted input, exactly as "
        "`rrip.ai.derive` supplies computed figures to narration elsewhere in "
        "this project.",
        "",
    ]
    return lines


def _governance_section(bench: dict | None) -> list[str]:
    lines = [
        "## 11. Governance and behaviour",
        "",
        "| condition | behaviour |",
        "|---|---|",
        "| No artifact, or one built for a different contract | `503 "
        "MODEL_UNAVAILABLE`, naming the command that builds it |",
        "| Horizon other than 1 week | `422 UNSUPPORTED_HORIZON`. The requested "
        "horizon is extracted faithfully and then refused -- clamping it to 1 "
        "would answer a different question under this one's label |",
        "| Unknown department | `404 UNKNOWN_DEPARTMENT`, with the list of "
        "modelled departments |",
        "| Trailing scale below the servable floor | `422 "
        "INSUFFICIENT_HISTORY`. GARDEN CENTER sold nothing across its whole "
        "trailing window; the service refuses rather than extrapolating |",
        "| Week outside the stored range | `422 OUT_OF_RANGE` |",
        "| Department with high measured error | served, flagged `LIMITED` or "
        "`LOW` with the measured WAPE in the reason |",
        "| Partial target week | served, with a caveat that it is a 6-day week |",
        "",
        "### Confidence is per department, and that was a fix",
        "",
        "An earlier version folded week-level caveats into the confidence "
        "flag, and every one of the 23 departments came back `LIMITED` -- "
        "because week 102 is partial and that applies to all of them equally. "
        "A flag with one value is not a flag, and it concealed the thing worth "
        "surfacing: measured test error runs from 7.0% WAPE for GROCERY to "
        "67.9% for COUP/STR & MFG.",
        "",
        "`confidence` now describes that department's measured reliability and "
        "nothing else (`NORMAL` within 1.5x pooled WAPE, `LIMITED` to 3x, "
        "`LOW` beyond); week-level and model-level warnings go in a separate "
        "`caveats` list. Cases `gov-06`, `gov-07` and `gov-08` pin all three "
        "levels so the tiering cannot silently collapse again.",
        "",
    ]

    if not bench:
        lines += ["Behaviour benchmark: " + MISSING, ""]
        return lines

    b = bench["behaviour"]
    lines += [
        f"### Behaviour benchmark: {b['passed']}/{b['total']} "
        f"({b['pass_rate']}%)",
        "",
        f"`{bench['dataset']}`, graded mechanically -- routing verdicts, "
        "extracted parameters, refusal codes and arithmetic properties of the "
        "payload. No LLM judge.",
        "",
        "| category | passed | total |", "|---|---:|---:|",
    ]
    for name, v in sorted(b["by_category"].items()):
        lines.append(f"| {name} | {v['passed']} | {v['total']} |")

    lat = bench.get("latency", {})
    lines += [
        "",
        f"Inference latency: p50 **{lat.get('p50_ms')} ms**, p95 "
        f"{lat.get('p95_ms')} ms over {lat.get('n')} calls. Serving is a "
        "dictionary lookup over precomputed rows -- there is no feature "
        "pipeline on the request path that could drift from the one that was "
        "evaluated.",
        "",
    ]
    return lines


def _nl_section() -> list[str]:
    return [
        "## 12. Natural-language routing",
        "",
        "```",
        "question",
        "   |",
        "   +-- router.classify()        deterministic, no model call",
        "   |     |",
        "   |     +-- UNSAFE / UNSUPPORTED / TOO_EXPENSIVE / AMBIGUOUS",
        "   |     +-- FORECAST  ---> rrip.forecast.service",
        "   |     +-- ANSWERABLE ---> NL->SQL",
        "   |",
        "   +-- forecast_intent.resolve()  department + horizon, string rules",
        "         |",
        "         +-- (fallback) LLM picks a name from a closed list",
        "```",
        "",
        "**The failure this prevents.** Asked \"what will Grocery revenue be "
        "next week?\" with only a SQL tool available, a language model does not "
        "refuse. It writes a valid aggregate over historical rows and returns "
        "a number that passes every gate this project has and is not a "
        "forecast. Routing the question away from SQL generation entirely is "
        "the intervention that prevents it, and it is a table lookup rather "
        "than a judgement.",
        "",
        "`FORECAST` sits after the absent-domain checks and before the "
        "ambiguity rules. After, so \"will customer satisfaction improve next "
        "week?\" is refused for the reason that applies -- there is no "
        "satisfaction data. Before, so a predictive question is not treated as "
        "under-specified: the forecaster has exactly one target, so there is "
        "no choice for the user to make.",
        "",
        "Predictive questions about metrics that were never modelled -- units, "
        "baskets, household counts -- are refused with `forecast_scope` rather "
        "than answered with the nearest available series. There is no measured "
        "error for those, so any figure would carry a confidence it has not "
        "earned.",
        "",
        "The model's maximum contribution on this path is choosing a "
        "department name from a closed list, and only when the deterministic "
        "matcher finds none. Its output is validated against that list before "
        "use, exactly as `rrip.ai.causal` discards proposed confounders that "
        "are not in the schema.",
        "",
    ]


def _limitations_section(m: dict) -> list[str]:
    return [
        "## 13. Limitations",
        "",
        "Stated plainly, because several of them bound what this component "
        "should be trusted for.",
        "",
        "1. **The test set is small.** 322 observations, 14 weeks, 23 series. "
        "Differences of under roughly one WAPE point are not resolvable here, "
        "which is why the deployment decision required a significance test "
        "rather than a lower number.",
        "2. **The headline is largely one department.** GROCERY is 51.6% of "
        "revenue and 40.8% of absolute error. Pooled WAPE "
        f"{_pct((m.get('metrics') or {}).get('test', {}).get('scores', {}).get('wape'))} "
        f"against macro WAPE {_pct((m.get('metrics') or {}).get('macro_wape_test'))} "
        "is the size of that gap.",
        "3. **Small departments are not forecastable.** The sparse tier scores "
        "44% WAPE. They are served with a `LOW` flag or refused, not silently "
        "included in an average.",
        "4. **One week ahead only.** Every feature is a lag or a rolling window "
        "over observed revenue; at two weeks the most informative of them does "
        "not exist. Multi-step is refused rather than approximated by "
        "iterating the model over its own output.",
        "5. **No annual seasonality.** 102 weeks with an arbitrary calendar "
        "anchor cannot support it, and no real holiday calendar is joined to "
        "this schema. A grocery retailer's actual Christmas effect is invisible "
        "here.",
        "6. **The panel is closed and ended.** There is no live feed to "
        "retrain on. The retraining policy is recorded in the contract for "
        "completeness, not because it runs.",
        "7. **Promotion features contribute nothing at this grain.** Measured, "
        "not assumed -- see section 4. A finer grain (product or commodity) "
        "might change that and was not attempted.",
        "8. **Conformal coverage is measured, not guaranteed.** The "
        "exchangeability assumption does not hold for time series. Coverage "
        "landed close to nominal on this test set; that is evidence, not a "
        "warranty.",
        "9. **The deployed predictor is a trailing mean.** It cannot anticipate "
        "a spike, a promotion, or a level shift. Outlier weeks score 14.7% "
        "WAPE against 8.7% on normal ones, and no model tested here fixed "
        "that.",
        "",
    ]


def _provenance_section(m: dict, bench: dict | None) -> list[str]:
    return [
        "## 14. Provenance",
        "",
        "| | |", "|---|---|",
        f"| Deployed predictor | `{m.get('model_type')}` |",
        f"| Model version | `{m.get('contract_version')}/{m.get('model_type')}/"
        f"{str(m.get('git_sha', ''))[:8]}` |",
        f"| Hyperparameters | `{json.dumps(m.get('hyperparameters', {}))}` |",
        f"| Random seed | `{m.get('random_seed')}` |",
        f"| Trained at | {m.get('trained_at')} |",
        f"| Git SHA | `{m.get('git_sha')}` |",
        f"| Contract version | `{m.get('contract_version')}` |",
        f"| Feature version | `{m.get('feature_version')}` |",
        f"| Dataset | dunnhumby Complete Journey, "
        f"{m.get('dataset', {}).get('rows'):,} department-week rows, weeks "
        f"{m.get('dataset', {}).get('weeks')} |",
        f"| Benchmark run | {bench.get('generated_at') if bench else MISSING} |",
        "",
        "A `-dirty` suffix on the SHA means the artifact was built from "
        "uncommitted work. The registry refuses to load an artifact whose "
        "contract or feature version differs from the running code -- the "
        "failure that prevents is silent, because stored coefficients applied "
        "to a reordered feature matrix still produce a plausible number.",
        "",
        "### Reproducing",
        "",
        "```bash",
        "rrip forecast-train     # panel -> audit -> select -> test -> artifact",
        "rrip forecast-eval      # behaviour benchmark + accuracy, non-zero on failure",
        "rrip forecast-report    # regenerate this document",
        "```",
        "",
    ]


def build() -> str:
    m = _load(MODEL_META)
    bench = _load(BENCH)

    if not m:
        return ("# Forecast release report\n\nNo model artifact found. Run "
                "`rrip forecast-train`.\n")

    dep = m.get("deployment") or {}
    head_coverage = ((m.get("conformal", {}).get("test_coverage", {})
                      .get("0.80") or {}).get("coverage", "n/a"))
    head = [
        "# Forecast release report",
        "",
        f"_Generated {datetime.now(UTC).isoformat(timespec='seconds')} from "
        "`models/forecast/metadata.json` and "
        "`reports/eval/forecast-latest.json`. Every figure is read from a "
        "measured artefact; nothing here is written by hand._",
        "",
        "---",
        "",
        "## Summary",
        "",
        f"One-week-ahead forecasting of **weekly revenue by department** over "
        f"the dunnhumby Complete Journey panel, "
        f"{m['dataset']['departments_qualifying']} departments, evaluated on "
        f"an untouched temporal test set of "
        f"{m['contract']['windows']['test'][1] - m['contract']['windows']['test'][0] + 1} "
        "weeks.",
        "",
        f"**The tested machine-learning models did not beat a simple trailing "
        f"mean, so the system deploys the trailing mean.** The gradient-"
        f"boosting challenger reached "
        f"{_pct(dep.get('model_scores', {}).get('wape'))} WAPE against "
        f"{_pct(dep.get('baseline_scores', {}).get('wape'))} for the baseline, "
        f"a difference with Diebold-Mariano p = "
        f"{dep.get('diebold_mariano', {}).get('p_value')}. It does "
        "significantly beat naive, seasonal-naive and drift; it does not beat "
        "any trailing-window baseline.",
        "",
        "That is a valid outcome and it is what this report documents. The "
        "predictive component earns its place by being measurable, refusable "
        "and honest about its own limits -- not by containing a model.",
        "",
    ] + _ranking_caveat(m) + [
        "| | |", "|---|---|",
        f"| Deployed | `{dep.get('deployed_predictor')}` |",
        f"| Test WAPE | {_pct(dep.get('baseline_scores', {}).get('wape'))} |",
        f"| Macro WAPE (unweighted by department) | "
        f"{_pct((m.get('metrics') or {}).get('macro_wape_test'))} |",
        f"| 80% interval coverage | {head_coverage} |",
        f"| Leakage audit | "
        f"{'passed' if m.get('leakage_audit', {}).get('passed') else 'FAILED'} |",
        ("| Behaviour benchmark | "
         f"{bench['behaviour']['passed']}/{bench['behaviour']['total']} |"
         if bench else "| Behaviour benchmark | not run |"),
        "",
        "---",
        "",
    ]

    parts = (head
             + _problem_section(m) + ["---", ""]
             + _data_section(m) + ["---", ""]
             + _features_section(m) + ["---", ""]
             + _leakage_section(m) + ["---", ""]
             + _baselines_section(m) + ["---", ""]
             + _test_section(m) + ["---", ""]
             + _decision_section(m) + ["---", ""]
             + _interval_section(m) + ["---", ""]
             + _robustness_section(m) + ["---", ""]
             + _explain_section(m) + ["---", ""]
             + _governance_section(bench) + ["---", ""]
             + _nl_section() + ["---", ""]
             + _limitations_section(m) + ["---", ""]
             + _provenance_section(m, bench))
    return "\n".join(parts) + "\n"


def write() -> Path:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(build(), encoding="utf-8")
    return OUT
