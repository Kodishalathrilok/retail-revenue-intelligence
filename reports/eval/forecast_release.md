# Forecast release report

_Generated 2026-10-02T06:12:11+00:00 from `models/forecast/metadata.json` and `reports/eval/forecast-latest.json`. Every figure is read from a measured artefact; nothing here is written by hand._

---

## Summary

One-week-ahead forecasting of **weekly revenue by department** over the dunnhumby Complete Journey panel, 23 departments, evaluated on an untouched temporal test set of 14 weeks.

**The tested machine-learning models did not beat a simple trailing mean, so the system deploys the trailing mean.** The gradient-boosting challenger reached 9.86% WAPE against 9.85% for the baseline, a difference with Diebold-Mariano p = 0.99227 (model vs trailing mean 4). It does significantly beat naive, seasonal-naive and drift; it does not beat any trailing-window baseline.

That is a valid outcome and it is what this report documents. The predictive component earns its place by being measurable, refusable and honest about its own limits -- not by containing a model.

### Read this before the tables

**The deployed predictor is not the best predictor on the test set.** `trailing_mean_4` ranks **4 of the 8 predictors scored** on weeks 88-101 (3 of the 6 registered baselines). It was chosen on validation, and it stays in place deliberately:

- **Reselecting on test would burn the only untouched measurement in this project.** Adopting whichever candidate scored best on the test weeks converts that number from an unbiased estimate into a selection statistic, and there is no second held-out set to recover one from.
- **The gaps are not resolvable anyway.** The Diebold-Mariano tests in this report compare the challenger model with each baseline; none compares one baseline with another. The model cannot be told apart from the deployed baseline (model vs trailing mean 4, p = 0.99227), and it cannot be told apart from the two baselines ranked above it either (model vs trailing mean 8, p = 0.25873; model vs trailing median 8, p = 0.16945). That is indirect evidence that the three are not separable on this test set, not a direct test of it.
- **There is direct evidence those gaps are noise.** The ordering of the trailing-window baselines is *exactly reversed* between validation and test:

  | rank | validation (rolling-origin) | test |
  |---|---|---|
  | 1 | `trailing_mean_4` **(deployed)** | `trailing_median_8` |
  | 2 | `trailing_mean_8` | `trailing_mean_8` |
  | 3 | `trailing_median_8` | `trailing_mean_4` **(deployed)** |

  `seasonal_naive_52` makes the same point from the other direction: it was **rejected** on a weeks 28-101 measurement (8.72% WAPE against 7.93% for an 8-week trailing mean) and comes **first** on the test weeks at 8.85%. A ranking that inverts between two windows of the same panel is measuring the window, not the predictor.

These predictors are one predictor with several spellings, and the choice among them is not a result. That is the finding, and it is the strongest part of this module -- stronger than any accuracy number below.

| | |
|---|---|
| Deployed | `trailing_mean_4` |
| Test WAPE | 9.85% |
| Macro WAPE (unweighted by department) | 31.62% |
| 80% interval coverage | 0.8075 |
| Leakage audit | passed |
| Behaviour benchmark | 30/30 |

---

## 1. What is being predicted

**Target.** `weekly_department_revenue` = `sum(sales_value)`, in USD.

| | |
|---|---|
| Grain | department x week |
| Horizon | 1 week |
| Prediction time | end of week t; the target is week t+1 |
| Departments modelled | 23 of 43 |
| Contract version | `forecast_v1` |
| Feature version | `1.0.0` |

**Why this target and not another.** Household-level weekly spend is mostly zeros over a closed 2,500-household panel with no acquisition process to learn. Daily revenue is ruled out by this project's own semantic layer, which declares day-of-week an absent domain: dunnhumby publishes `DAY 1..711` with no calendar anchor, so the dominant seasonality of a daily series is a modelling convention rather than data. Department x week survives because it is the grain a supermarket plans on, it is already the grain of the published tier (`pub_weekly_revenue_by_dept`), and it has enough series to pool across.

**Inclusion rule.** mean training-week revenue >= $1.00 and >= 50% of training weeks non-zero, evaluated on weeks 28-73 only. Fitted on training weeks alone -- a rule computed over the whole panel would use test-period activity to decide which series are modellable, which is selection on the outcome.

Departments modelled: `CHEF SHOPPE`, `COSMETICS`, `COUP/STR & MFG`, `DELI`, `DRUG GM`, `FLORAL`, `FROZEN GROCERY`, `GARDEN CENTER`, `GROCERY`, `KIOSK-GAS`, `MEAT`, `MEAT-PCKGD`, `MISC SALES TRAN`, `MISC. TRANS.`, `NUTRITION`, `PASTRY`, `PRODUCE`, `RESTAURANT`, `SALAD BAR`, `SEAFOOD`, `SEAFOOD-PCKGD`, `SPIRITS`, `TRAVEL & LEISUR`.

---

## 2. Data, windows and the hazard that shaped them

### The panel-recruitment ramp

dunnhumby recruits households at the start of observation rather than acquiring them over time. Measured on the loaded data:

| by week | households transacted | share of panel |
|---:|---:|---:|
| 4 | 448 | 17.9% |
| 8 | 968 | 38.7% |
| 12 | 1,587 | 63.5% |
| 16 | 2,269 | 90.8% |
| **20** | **2,493** | **99.7%** |

Total weekly revenue roughly quadruples across those twenty weeks and none of it is demand -- it is enrolment. A model trained across that boundary learns a growth rate that does not exist, and every rolling statistic computed over it is a statistic of two different populations.

So `HISTORY_FLOOR_WEEK = 20` is a hard floor: no feature, for any target week, may read a week before it. That is stronger than starting training later, because a rolling mean at the start of training would otherwise still reach back into the ramp.

Weeks 1, 102 are partial (5 and 6 days) and are excluded from targets and from feature history entirely.

### Splits

| split | weeks | n weeks | rows | revenue |
|---|---|---:|---:|---:|
| train | 28-73 | 46 | 1,058 | $3,980,765 |
| validation | 74-87 | 14 | 322 | $1,218,139 |
| test | 88-101 | 14 | 322 | $1,295,967 |

### Why random splitting would be invalid here

Not merely because it is a time series. Three specific mechanisms:

1. **The features are the neighbouring rows.** The 8-week window for target week *w* averages weeks *w-8..w-1*. Randomly assigning week *w* to test and week *w-1* to train places the test row's target inside a training row's feature window -- the training set literally contains the test answer, averaged with seven others.
2. **The preprocessing is fitted.** The inclusion rule, the normalisation scale and the per-department encodings are fitted on training weeks. Under a random split they would be fitted on data surrounding the test weeks, so the pipeline would have seen the future even if no model had.
3. **It answers the wrong question.** Interpolating a held-out week from the weeks either side of it is not forecasting. A planner has only the past.

Rolling-origin folds used for selection:

- `fold 0: train 28-45 -> validate 46-52`
- `fold 1: train 28-52 -> validate 53-59`
- `fold 2: train 28-59 -> validate 60-66`
- `fold 3: train 28-66 -> validate 67-73`
- `fold 4: train 28-73 -> validate 74-80`
- `fold 5: train 28-80 -> validate 81-87`

---

## 3. Features and the leakage boundary

24 features, each declaring the newest and oldest week it reads relative to the prediction cutoff. The registry is machine-readable so the audit can check it rather than trust it.

| feature | family | reads weeks | why it is legal |
|---|---|---|---|
| `rev_lag1_ratio` | lag | t | Revenue in week t, over trailing scale. |
| `rev_lag2_ratio` | lag | t-1 | Revenue in week t-1, over trailing scale. |
| `rev_lag3_ratio` | lag | t-2 | Revenue in week t-2, over trailing scale. |
| `rev_lag4_ratio` | seasonal | t-3 | Revenue in week t-3 -- the seasonal lag, since the target is t+1 and the cycle is 4 weeks. |
| `roll4_ratio` | rolling | t-3 .. t | Mean of weeks t-3. |
| `rollmed8_ratio` | rolling | t-7 .. t | Median of weeks t-7. |
| `rollstd8_ratio` | rolling | t-7 .. t | Standard deviation of weeks t-7. |
| `momentum_ratio` | rolling | t-7 .. t | roll4 minus roll8, over the scale. |
| `trend_slope_ratio` | rolling | t-7 .. t | OLS slope across weeks t-7. |
| `zeros_in_window` | rolling | t-7 .. t | Count of zero-revenue weeks in t-7. |
| `panel_rev_lag1_ratio` | panel | t-7 .. t | Panel-wide revenue in week t over its own 8-week trailing mean. |
| `panel_hh_lag1_ratio` | panel | t-7 .. t | Active households panel-wide in week t over their 8-week trailing mean. |
| `panel_baskets_lag1_ratio` | panel | t-7 .. t | Panel-wide baskets in week t over their 8-week trailing mean. |
| `dept_share_lag1` | panel | t | This department's share of panel revenue in week t. |
| `dept_share_delta` | panel | t-7 .. t | Share in week t minus mean share over t-7. |
| `promo_display_pct_lag1` | promo | t | Share of this department's promo rows in week t that were on in-store display. |
| `promo_mailer_pct_lag1` | promo | t | Share of this department's promo rows in week t that appeared in a mailer. |
| `promo_display_delta` | promo | t-7 .. t | Display share in week t minus its mean over t-7. |
| `promo_rows_ratio` | promo | t-7 .. t | Promoted product-store rows for this department in week t over their 8-week mean. |
| `campaigns_active_lag1` | campaign | t | Marketing campaigns running in week t. |
| `campaign_households_lag1` | campaign | t | Households enrolled in a campaign running in week t. |
| `dept_volatility` | identity | t-7 .. t | Coefficient of variation of this department's weekly revenue, over training weeks only. |
| `dept_persistence` | identity | t-7 .. t | Lag-1 autocorrelation of this department's weekly revenue, over training weeks only. |
| `dept_log_scale` | identity | t-7 .. t | log10 of the department's mean training-week revenue. |

The target is week *t+1*, so every offset at or below 0 is history.

### What is deliberately excluded

- **Next week's promotions and campaigns.** A real retailer knows its mailer plan, so this is stricter than production would need. It is drawn here because "we would have known it" is an assumption about an operating process this dataset cannot evidence, and a boundary resting on an unverifiable claim is not a boundary. The cost of that restraint is measured below rather than assumed.
- **Annual seasonality (lag 52).** Tested and rejected -- see the baselines section.
- **Absolute time.** No week index or trend term. A linear model would extrapolate it past the data and a tree model cannot extrapolate it at all; neither behaviour is one this panel can justify.

### Target transformation

Departments span three orders of magnitude (GROCERY ~$44k/week, FROZEN GROCERY ~$8/week). The model predicts a **ratio to the 8-week trailing mean**, which is known at prediction time:

```
scale s(d,t) = mean revenue for department d over weeks t-7..t
model target = revenue(d, t+1) / s(d,t)
forecast     = predicted_ratio * s(d,t)
```

Predicting the constant 1.0 reproduces the 8-week trailing-mean baseline exactly, so the model starts level with a baseline and has to earn any departure from it. Training weights each row by `s(d,t)`, so weighted absolute error in ratio space *is* absolute error in dollars -- the training objective is the reported metric rather than a proxy for it.

Missing-data policy: absent department-week means zero revenue, not missing.

---

## 4. Leakage controls

**Audit result: PASSED** (6 checks). The audit runs *before* any model is fitted, so it cannot be argued against a number that already exists.

| check | result | detail |
|---|---|---|
| `declared_cutoff` | pass | no registered feature reads a week after the prediction cutoff |
| `history_floor` | pass | earliest target week 28 reads back to week 20, at or after the floor 20 |
| `split_order` | pass | train, validation and test are contiguous, ordered and non-overlapping, and no rolling-origin fold reaches the test window |
| `fitted_scope` | pass | department statistics fitted on weeks (28, 73), which is the training window |
| `target_independence` | pass | corrupting a week's revenue, promotions and campaigns changed no feature on the row whose target is that week |
| `future_window` | pass | features for target week 70 are identical whether built from the full panel or from a panel truncated at the cutoff |

### The bug this audit actually caught

On its first run the audit failed `target_independence` and `future_window`, both on `rollstd8_ratio`. The cause was a rolling window that was not grouped by department: `df.groupby('department')[col].shift(1).rolling(8)` looks correct and is not -- `SeriesGroupBy.shift` returns a plain Series, so the rolling that follows runs across the whole frame and each department's early windows are filled with the tail of the previous department.

The mean and the median were unaffected in the delivered rows, because the contaminated windows all land in weeks below the first target. The **variance** was affected everywhere, because pandas computes rolling variance with an add/remove accumulator: once a value from another department has passed through it, the running sum of squares carries the rounding error for the rest of the series. Corrupting one week by a factor of 1000 moved `rollstd8_ratio` by 7e-05 relative in departments whose own windows had not changed.

Fixed by grouping the rolling explicitly and by computing the standard deviation two-pass. Both are now permanent regression tests in `tests/test_forecast_leakage.py`.

### What the strict cutoff costs, measured

A variant that deliberately reads the target week's promotion and campaign activity was trained and scored on the same folds. A leakage test that never constructs a leak proves the code compiles, not that the guard works.

| variant | WAPE |
|---|---:|
| strict (deployed boundary) | 6.70% |
| oracle (reads week *t+1* promotions) | 6.86% |

**The leak makes it worse, by 2.4%.** Department-week aggregates of display and mailer share across 92,353 products are too coarse to predict department revenue, so the strict boundary costs nothing here. That is a more useful finding than a large number would have been: it says there is no accuracy argument for relaxing the cutoff, and any future version that adds promotion features needs a finer grain to justify itself.

---

## 5. Baselines

The measured autocorrelation decides which baseline is the real competitor, and on this data it is **not** the naive one. Post-ramp weekly revenue has lag-1 autocorrelation near zero at panel level (+0.07 over weeks 28-101, -0.04 over weeks 40-101), so last week's figure is a poor predictor of next week's.

Reporting a model as *n%* better than naive would therefore be measuring the weakness of the naive rule. Every comparison in this report is against the **best** baseline, identified on validation like any other choice.

### Rolling-origin validation (train + validation only)

| baseline | MAE | RMSE | WAPE |
|---|---:|---:|---:|
| `trailing_mean_4` | 273.5 | 719.5 | 7.16% |
| `trailing_mean_8` | 279.7 | 722.9 | 7.33% |
| `trailing_median_8` | 287.6 | 752.2 | 7.53% |
| `seasonal_naive_4` | 314.0 | 716.4 | 8.23% |
| `naive` | 354.9 | 944.6 | 9.29% |
| `drift` | 376.4 | 1,000.4 | 9.86% |

Best baseline: **`trailing_mean_4`** at 7.16%.

### Seasonality: what was tested and what was rejected

The grain is already weekly, so the candidate seasonal period is annual. **Lag 52 was tested and rejected**, for two independent reasons:

- *Structure.* The panel is 102 weeks. A 52-week lookback for the first target week would reach week -24, so lag 52 cannot be a feature at all without discarding half the training data.
- *Measurement.* Scored as a baseline over weeks 28-101 it reached WAPE 8.72% against 7.93% for an 8-week trailing mean -- worse than having no seasonal term.

What the data does show is a **four-week cycle**: lag-4 autocorrelation is +0.36 on total revenue post-ramp, +0.43 for GROCERY, +0.52 for MEAT-PCKGD, +0.43 for PRODUCE -- consistent with monthly household budgeting. So the seasonal-naive baseline here is lag 4, chosen from measured autocorrelation rather than from convention.

A lag-52 baseline *is* computable on the test weeks (88-101 look back to 36-49, both above the history floor), so it is scored there too and appears in the test table below. It does well there. That is discussed rather than acted on -- see section 7.

### Model candidates on the same folds

| candidate | family | MAE | RMSE | WAPE |
|---|---|---:|---:|---:|
| `hgb(lr=0.02,leaves=7,min_leaf=40)` | GradientBoostRatio | 255.6 | 633.5 | 6.70% |
| `hgb(lr=0.05,leaves=7,min_leaf=40)` | GradientBoostRatio | 258.3 | 631.2 | 6.76% |
| `hgb(lr=0.05,leaves=15,min_leaf=20)` | GradientBoostRatio | 260.2 | 638.3 | 6.82% |
| `rf(depth=6,min_leaf=20)` | RandomForestRatio | 263.6 | 657.9 | 6.90% |
| `rf(depth=10,min_leaf=10)` | RandomForestRatio | 267.5 | 660.2 | 7.01% |
| `ridge(alpha=30.0)` | RidgeRatio | 285.5 | 731.9 | 7.48% |
| `ridge(alpha=10.0)` | RidgeRatio | 285.9 | 732.3 | 7.49% |
| `ridge(alpha=3.0)` | RidgeRatio | 286.1 | 732.6 | 7.49% |
| `ridge(alpha=1.0)` | RidgeRatio | 286.2 | 732.7 | 7.50% |
| `ridge(alpha=0.3)` | RidgeRatio | 286.3 | 732.7 | 7.50% |

Selected on validation: **`hgb(lr=0.02,leaves=7,min_leaf=40)`** at 6.70%.

### A note on the linear model

Ridge initially scored WAPE 8.32% -- worse than every baseline including naive. That was a preprocessing failure rather than a verdict on linear models: the ratio features are heavy-tailed (`rev_lag1_ratio` reaches 8.0 against a median of 0.98) and squared loss on unbounded inputs spends the fit on a handful of sparse-department weeks. Clipping each feature to its 1st-99th training percentile took it to 7.49%.

It still loses to the best baseline, and that is the honest result -- but the first number would have made the tree model look better than it is, so the fix is part of the model and the reason is recorded here. Winsorisation bounds are fitted on training rows and stored in the artifact, so validation and test are clipped with training bounds.

Also measured: an **unweighted** ridge scores WAPE 45.6%. The scale-weighting is not a refinement, it is what makes a pooled model over three orders of magnitude work at all.

---

## 6. Test performance

Weeks **88-101**, 322 observations across 23 departments. Untouched during model and hyperparameter selection -- `rrip.forecast.split.test_frame` raises unless passed an explicit unlock token, so every place a final number is produced is greppable.

| predictor | MAE | RMSE | WAPE | sMAPE |
|---|---:|---:|---:|---:|
| `seasonal_naive_52` | 356.2 | 1,089.9 | 8.85% | 37.31% |
| `trailing_median_8` | 373.4 | 978.8 | 9.28% | 30.45% |
| `trailing_mean_8` | 378.4 | 971.6 | 9.40% | 33.29% |
| `trailing_mean_4` **(deployed)** | 396.5 | 1,035.3 | 9.85% | 29.81% |
| **challenger model** | 396.7 | 1,017.1 | 9.86% | 33.18% |
| `seasonal_naive_4` | 483.9 | 1,204.2 | 12.02% | 35.34% |
| `naive` | 487.2 | 1,402.6 | 12.11% | 33.93% |
| `drift` | 507.4 | 1,451.2 | 12.61% | 35.35% |

**MAPE is deliberately absent.** This panel contains genuine zero weeks -- FROZEN GROCERY averages $7.90 a week and lands on zero repeatedly -- and MAPE is undefined at zero and unbounded near it. WAPE is the pooled ratio of the same quantities and stays finite. sMAPE is reported alongside because WAPE is dollar-weighted and GROCERY is half the dollars.

### Is the difference real?

Diebold-Mariano on absolute-error loss, with the Harvey-Leybourne-Newbold small-sample correction. Every row tests the challenger model against one baseline; no row tests one baseline against another. Negative mean loss differential means the challenger has lower error.

| comparison | mean loss diff | p |
|---|---:|---:|
| model vs naive | -90.5423 | 0.02974 |
| model vs seasonal naive 4 | -87.1821 | 0.00518 |
| model vs trailing mean 8 | 18.2616 | 0.25873 |
| model vs trailing median 8 | 23.2861 | 0.16945 |
| model vs trailing mean 4 | 0.1744 | 0.99227 |
| model vs drift | -110.6997 | 0.01191 |

The challenger **significantly beats every weak baseline** -- naive (p = 0.030), seasonal-naive-4 (p = 0.005), drift (p = 0.012) -- and is **indistinguishable from every strong one**. Against the three trailing-window baselines it is nominally *worse* on two of them and level on the third. It is closer than the deployed baseline on 47.5% of test rows: a coin flip.

| | pooled | macro (unweighted by department) |
|---|---:|---:|
| WAPE | 9.85% | 31.62% |

The gap between those two columns is the single most important caveat in this report. Pooled WAPE is dollar-weighted, GROCERY is 51.6% of revenue and carries 36.9% of the deployed predictor's absolute error, so the headline figure is substantially a statement about GROCERY.

---

## 7. Deployment decision

### The rule, fixed before the test set was unlocked

> The fitted model is deployed only if BOTH hold on the untouched test weeks: (1) its WAPE is lower than the baseline selected on validation, and (2) a Diebold-Mariano test on absolute-error loss rejects equal accuracy at p < 0.05 in the model's favour. Otherwise the baseline is deployed and the model is retained as a measured challenger. Condition (2) exists because at n=322 a WAPE difference of a few hundredths of a point is not a result.

### The outcome

| | |
|---|---|
| Challenger | `hgb(lr=0.02,leaves=7,min_leaf=40)` |
| Challenger test WAPE | 9.86% |
| Baseline | `trailing_mean_4` |
| Baseline test WAPE | 9.85% |
| Diebold-Mariano p (model vs trailing mean 4) | 0.99227 |
| **Deployed** | **`trailing_mean_4`** (baseline) |

hgb(lr=0.02,leaves=7,min_leaf=40) did not beat trailing_mean_4 on the test weeks (WAPE 9.86% against 9.85%; Diebold-Mariano p = 0.99227). The baseline is deployed. This is a valid outcome and it is reported as measured rather than worked around -- a simpler predictor that performs as well is the better system.

### This is Outcome C, and it is reported as one

The tested machine-learning models did not beat a four-week trailing mean by any margin this dataset can resolve, so the system uses the simpler predictor. Four independent lines of evidence point the same way, which is why this reads as a property of the data rather than a failed experiment:

1. Post-ramp lag-1 autocorrelation of weekly revenue is near zero.
2. Running the forecast on **one-week-stale data costs nothing** -- it is marginally *better* (see section 9). The most recent week carries no usable signal.
3. Giving the model **next week's promotions makes it worse**, so the promotional features carry nothing at this grain.
4. A 52-week lag performs as well as anything else on the test weeks, which is what a series with little exploitable structure looks like.

Taken together: department weekly revenue in this panel is, to the accuracy 322 test observations can measure, **a local level plus noise**. Estimating the level is the whole of the job, and a trailing mean estimates it.

### What was NOT done, and why

`seasonal_naive_52` scored 8.85% on the test weeks -- the best of any predictor here -- and `trailing_median_8` also beat the deployed baseline. **Neither was adopted.** Switching to whichever candidate scored best on the test set is precisely what makes a test score stop being an estimate of anything: it would convert the one untouched measurement in this report into a selection statistic.

The deployed baseline was chosen on validation, where it beat the 8-week mean at p = 0.008. Its test score of 9.85% is an unbiased estimate; the better-looking alternatives are not, and the instability between the two blocks is itself part of the finding.

### What is retained

The fitted model, its metadata and the entire benchmark stay in the repository. The decision is auditable rather than a deleted branch, and `rrip forecast-train` will promote the model automatically if a future contract or dataset makes it clear the rule.

---

## 8. Prediction intervals

**These are prediction intervals, not confidence intervals.** A confidence interval would cover the expected value; a prediction interval covers the single future observation. A planner ordering stock needs the second one, and it is materially wider.

Method: **split conformal** on scale-normalised residuals, calibrated on validation weeks [74, 87] against the deployed predictor (`trailing_mean_4`). Quantiles are taken of the *signed* residual, so the interval is asymmetric -- revenue is bounded below at zero and unbounded above. The lower bound is floored at zero.

| nominal | measured coverage | mean width | median width | below | above |
|---:|---:|---:|---:|---:|---:|
| 80% | **80.8%** | $3,409 | $375 | 28 | 34 |
| 95% | **94.7%** | $9,591 | $1,054 | 13 | 4 |

### The assumption, and why coverage is measured anyway

Conformal guarantees marginal coverage under **exchangeability** of calibration and test residuals. Time series are not exchangeable: if the later weeks are harder than the calibration weeks the interval is too narrow and the guarantee does not hold. So it is treated as a construction principle, not a claim, and realised coverage is measured on the temporal test set. Where the two disagree, the measured number is the true one.

Here they agree closely. That was **not** true of an earlier configuration: intervals calibrated on the challenger's residuals but applied to the deployed baseline's forecasts under-covered at 77.0% against 80% nominal, and 91.9% against 95%. Calibrating on the deployed predictor's own residuals fixed it. An interval fitted to one predictor's errors and wrapped around another's is an interval for a forecast nobody serves.

Mean width exceeds median width several-fold because the mean is dominated by GROCERY. The relevant figure for a small department is the median.

---

## 9. Robustness

Every slice below is reported as measured. A robustness section where everything passes has not tested anything.

### Time drift

| weeks | n | MAE | WAPE |
|---|---:|---:|---:|
| 88-92 | 115 | 391.3 | 9.45% |
| 93-97 | 115 | 371.5 | 9.66% |
| 98-101 | 92 | 434.3 | 10.58% |

Drift first block to last: **1.123 percentage points**. Positive drift means later weeks are forecast less accurately, which is what a model going stale looks like. At 14 test weeks this is a direction, not a trend estimate.

### Department size

| tier | departments | n | MAE | WAPE |
|---|---:|---:|---:|---:|
| large (>= $1k/wk) | 10 | 140 | 818.7 | 9.13% |
| medium ($100-$1k/wk) | 8 | 112 | 109.8 | 31.26% |
| sparse (< $100/wk) | 5 | 70 | 10.9 | 43.17% |

The sparse tier is expected to score badly: those series hit zero repeatedly and a percentage error on a $7 week is close to meaningless. They are reported rather than excluded, and the API refuses to serve a department whose trailing scale is below $5.

This is the clearest limitation in the system. The sparse tier is not forecastable at this panel size and is flagged `LOW` confidence at the API; a department whose trailing scale falls below $5 is refused outright.

### Department concentration

- Pooled WAPE **9.85%**, macro WAPE **31.62%**.
- `GROCERY` carries **36.9%** of all absolute error.

| department | n | MAE | WAPE | error share | revenue share |
|---|---:|---:|---:|---:|---:|
| `GROCERY` | 14 | 3,363.1 | 7.04% | 36.9% | 51.6% |
| `DRUG GM` | 14 | 1,275.1 | 10.15% | 14.0% | 13.6% |
| `MEAT` | 14 | 689.6 | 11.53% | 7.6% | 6.5% |
| `PRODUCE` | 14 | 606.6 | 9.90% | 6.7% | 6.6% |
| `MEAT-PCKGD` | 14 | 504.9 | 11.23% | 5.5% | 4.9% |
| `MISC SALES TRAN` | 14 | 490.3 | 32.56% | 5.4% | 1.6% |
| `KIOSK-GAS` | 14 | 483.2 | 8.55% | 5.3% | 6.1% |
| `DELI` | 14 | 396.1 | 13.21% | 4.3% | 3.2% |
| `FLORAL` | 14 | 284.6 | 60.54% | 3.1% | 0.5% |
| `PASTRY` | 14 | 239.9 | 17.70% | 2.6% | 1.5% |
| `SEAFOOD-PCKGD` | 14 | 164.6 | 20.56% | 1.8% | 0.9% |
| `NUTRITION` | 14 | 138.1 | 11.55% | 1.5% | 1.3% |
| `COSMETICS` | 14 | 112.3 | 27.89% | 1.2% | 0.4% |
| `MISC. TRANS.` | 14 | 97.6 | 65.39% | 1.1% | 0.2% |
| `SPIRITS` | 14 | 77.4 | 30.38% | 0.9% | 0.3% |
| `SEAFOOD` | 14 | 58.3 | 16.46% | 0.6% | 0.4% |
| `SALAD BAR` | 14 | 47.5 | 13.64% | 0.5% | 0.4% |
| `GARDEN CENTER` | 14 | 36.4 | 117.09% | 0.4% | 0.0% |
| `RESTAURANT` | 14 | 17.6 | 48.50% | 0.2% | 0.0% |
| `TRAVEL & LEISUR` | 14 | 11.7 | 45.31% | 0.1% | 0.0% |
| `CHEF SHOPPE` | 14 | 10.6 | 26.59% | 0.1% | 0.0% |
| `COUP/STR & MFG` | 14 | 7.9 | 67.91% | 0.1% | 0.0% |
| `FROZEN GROCERY` | 14 | 6.5 | 53.70% | 0.1% | 0.0% |

### Promotion periods

| band | n | MAE | WAPE |
|---|---:|---:|---:|
| high promotion | 161 | 556.2 | 9.16% |
| low promotion | 161 | 236.8 | 11.97% |

Split on the target week's realised display share, which is an analysis-time quantity. The model had only the previous week's.

Accuracy is *better* in heavily promoted weeks, not worse. The likely mechanism is that promotion intensity correlates with department size -- larger departments carry more promoted product-store rows -- so the split is partly a proxy for the size split above.

### Outlier weeks

| band | n | MAE | WAPE |
|---|---:|---:|---:|
| normal (|z| < 2.0) | 286 | 302.3 | 8.30% |
| outlier (|z| >= 2.0) | 36 | 1,144.8 | 16.20% |

Spikes are where a trailing-mean forecast is guaranteed to be wrong, and no model without the cause of the spike in its features can fix that. Reported so the failure is sized rather than implied.

### Incomplete recent data

Simulated exactly: the forecast for week *w* is taken from the feature row built for week *w-1*, which used data through *w-2*. That is the situation where last week's sales have not finished loading and the planner runs the model anyway.

| run | MAE | WAPE |
|---|---:|---:|
| fresh | 408.9 | 10.16% |
| one week stale | 396.8 | 9.86% |

**Penalty: -0.302 percentage points** -- that is, running a week behind is marginally *better*. This is not a robustness win to celebrate; it is further evidence that the most recent week carries no exploitable signal, consistent with the near-zero lag-1 autocorrelation. It is one of the four findings behind the deployment decision.

---

## 10. Explainability

### The deployed predictor

The served explanation is the arithmetic: the four weekly revenue figures that were averaged, each with weight 0.25. They reconcile to the forecast exactly, and `payload-01` in the behaviour benchmark asserts that they sum to it within a cent on every run.

That is a stronger explanation than any attribution method produces for a tree ensemble, and it is one of the things gained by the model not having earned deployment.

### The challenger, retained for the benchmark

Permutation importance on validation data (increase in dollar MAE when a feature is shuffled), seeded so the table is reproducible.

| feature | importance |
|---|---:|
| Revenue four weeks ago (seasonal lag) | 28.14 |
| Store-wide active households last week | 7.79 |
| How much this department follows last week | 6.47 |
| Revenue three weeks ago | 5.80 |
| Last week's revenue | 4.24 |
| Store-wide revenue last week | 3.97 |
| Change in department share | 3.79 |
| Products in the mailer last week | 2.68 |
| 8-week median | 2.48 |
| 8-week volatility | 2.14 |

The four-week seasonal lag dominates, which is consistent with the measured lag-4 autocorrelation and is the one piece of structure beyond the local level that this data clearly contains.

**No LLM produces any of this.** When the narration layer describes a forecast it receives these fields as trusted input, exactly as `rrip.ai.derive` supplies computed figures to narration elsewhere in this project.

---

## 11. Governance and behaviour

| condition | behaviour |
|---|---|
| No artifact, or one built for a different contract | `503 MODEL_UNAVAILABLE`, naming the command that builds it |
| Horizon other than 1 week | `422 UNSUPPORTED_HORIZON`. The requested horizon is extracted faithfully and then refused -- clamping it to 1 would answer a different question under this one's label |
| Unknown department | `404 UNKNOWN_DEPARTMENT`, with the list of modelled departments |
| Trailing scale below the servable floor | `422 INSUFFICIENT_HISTORY`. GARDEN CENTER sold nothing across its whole trailing window; the service refuses rather than extrapolating |
| Week outside the stored range | `422 OUT_OF_RANGE` |
| Department with high measured error | served, flagged `LIMITED` or `LOW` with the measured WAPE in the reason |
| Partial target week | served, with a caveat that it is a 6-day week |

### Confidence is per department, and that was a fix

An earlier version folded week-level caveats into the confidence flag, and every one of the 23 departments came back `LIMITED` -- because week 102 is partial and that applies to all of them equally. A flag with one value is not a flag, and it concealed the thing worth surfacing: measured test error runs from 7.0% WAPE for GROCERY to 67.9% for COUP/STR & MFG.

`confidence` now describes that department's measured reliability and nothing else (`NORMAL` within 1.5x pooled WAPE, `LIMITED` to 3x, `LOW` beyond); week-level and model-level warnings go in a separate `caveats` list. Cases `gov-06`, `gov-07` and `gov-08` pin all three levels so the tiering cannot silently collapse again.

### Behaviour benchmark: 30/30 (100.0%)

`eval\datasets\forecast_v1.jsonl`, graded mechanically -- routing verdicts, extracted parameters, refusal codes and arithmetic properties of the payload. No LLM judge.

| category | passed | total |
|---|---:|---:|
| governance | 8 | 8 |
| payload | 6 | 6 |
| routing | 10 | 10 |
| scope | 6 | 6 |

Inference latency: p50 **0.2022 ms**, p95 0.3818 ms over 200 calls. Serving is a dictionary lookup over precomputed rows -- there is no feature pipeline on the request path that could drift from the one that was evaluated.

---

## 12. Natural-language routing

```
question
   |
   +-- router.classify()        deterministic, no model call
   |     |
   |     +-- UNSAFE / UNSUPPORTED / TOO_EXPENSIVE / AMBIGUOUS
   |     +-- FORECAST  ---> rrip.forecast.service
   |     +-- ANSWERABLE ---> NL->SQL
   |
   +-- forecast_intent.resolve()  department + horizon, string rules
         |
         +-- (fallback) LLM picks a name from a closed list
```

**The failure this prevents.** Asked "what will Grocery revenue be next week?" with only a SQL tool available, a language model does not refuse. It writes a valid aggregate over historical rows and returns a number that passes every gate this project has and is not a forecast. Routing the question away from SQL generation entirely is the intervention that prevents it, and it is a table lookup rather than a judgement.

`FORECAST` sits after the absent-domain checks and before the ambiguity rules. After, so "will customer satisfaction improve next week?" is refused for the reason that applies -- there is no satisfaction data. Before, so a predictive question is not treated as under-specified: the forecaster has exactly one target, so there is no choice for the user to make.

Predictive questions about metrics that were never modelled -- units, baskets, household counts -- are refused with `forecast_scope` rather than answered with the nearest available series. There is no measured error for those, so any figure would carry a confidence it has not earned.

The model's maximum contribution on this path is choosing a department name from a closed list, and only when the deterministic matcher finds none. Its output is validated against that list before use, exactly as `rrip.ai.causal` discards proposed confounders that are not in the schema.

---

## 13. Limitations

Stated plainly, because several of them bound what this component should be trusted for.

1. **The test set is small.** 322 observations, 14 weeks, 23 series. Differences of under roughly one WAPE point are not resolvable here, which is why the deployment decision required a significance test rather than a lower number.
2. **The headline is largely one department.** GROCERY is 51.6% of revenue and 36.9% of absolute error. Pooled WAPE 9.85% against macro WAPE 31.62% is the size of that gap.
3. **Small departments are not forecastable.** The sparse tier scores 43% WAPE. They are served with a `LOW` flag or refused, not silently included in an average.
4. **One week ahead only.** Every feature is a lag or a rolling window over observed revenue; at two weeks the most informative of them does not exist. Multi-step is refused rather than approximated by iterating the model over its own output.
5. **No annual seasonality.** 102 weeks with an arbitrary calendar anchor cannot support it, and no real holiday calendar is joined to this schema. A grocery retailer's actual Christmas effect is invisible here.
6. **The panel is closed and ended.** There is no live feed to retrain on. The retraining policy is recorded in the contract for completeness, not because it runs.
7. **Promotion features contribute nothing at this grain.** Measured, not assumed -- see section 4. A finer grain (product or commodity) might change that and was not attempted.
8. **Conformal coverage is measured, not guaranteed.** The exchangeability assumption does not hold for time series. Coverage landed close to nominal on this test set; that is evidence, not a warranty.
9. **The deployed predictor is a trailing mean.** It cannot anticipate a spike, a promotion, or a level shift. Outlier weeks score 16.2% WAPE against 8.3% on normal ones, and no model tested here fixed that.

---

## 14. Provenance

| | |
|---|---|
| Deployed predictor | `trailing_mean_4` |
| Model version | `forecast_v1/trailing_mean_4/a9a2b619` |
| Hyperparameters | `{"window_weeks": 4, "statistic": "mean", "form": "mean of revenue in weeks t-3..t"}` |
| Random seed | `20260814` |
| Trained at | 2026-08-14T11:44:37+00:00 |
| Git SHA | `a9a2b6197a452fd024979a9dcc7b02c81536658b-dirty` |
| Contract version | `forecast_v1` |
| Feature version | `1.0.0` |
| Dataset | dunnhumby Complete Journey, 3,526 department-week rows, weeks [20, 101] |
| Benchmark run | 2026-08-14T15:56:42+00:00 |

A `-dirty` suffix on the SHA means the artifact was built from uncommitted work. The registry refuses to load an artifact whose contract or feature version differs from the running code -- the failure that prevents is silent, because stored coefficients applied to a reordered feature matrix still produce a plausible number.

### Reproducing

```bash
rrip forecast-train     # panel -> audit -> select -> test -> artifact
rrip forecast-eval      # behaviour benchmark + accuracy, non-zero on failure
rrip forecast-report    # regenerate this document
```

