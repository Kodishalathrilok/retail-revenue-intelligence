# Methodology notes

Eight decisions in this project were made wrongly first and corrected against
evidence. They are written up here because the corrections are more informative
than the final answers: each one would have produced a plausible-looking result
that was quietly wrong, and each was caught by a specific measurement rather
than by review.

---

## 1. The control group was defined too strictly

**The decision.** Phase 6c estimates the effect of a dunnhumby marketing
campaign using difference-in-differences. That requires a control group of
untreated households.

**What was wrong.** The first definition was *households enrolled in no campaign
at all*. This is intuitive and it is what "untreated" sounds like it should
mean. It is also wrong, and it fails in a way that is easy to miss: it produces
no error, only a smaller number.

The failure was found by a smoke test rather than by reasoning. Before the real
data arrived, the probe was run against a synthetic dataset shaped like
dunnhumby, deliberately seeded with defects — including one blanket campaign
enrolling every household in the panel. Under the strict definition, that single
campaign emptied the control group for *every other campaign in the dataset*.
The probe reported that no difference-in-differences was possible anywhere.

That conclusion was false. A campaign that ran on days 1–100 does not
contaminate a comparison of days 420–500. Treatment status only matters if it
overlaps the window being analysed.

**The correction.** The control group is now households not in this campaign and
not in any campaign whose window overlaps this one's *analysis window* — a
bounded 180-day pre-period through campaign end. Campaigns running at unrelated
times no longer disqualify anyone.

The stricter never-treated count is still computed and reported as
`strict_control_n`, because where it is non-empty it is the cleanest group
available. It simply does not drive the verdict.

**Why it matters.** On the real data, 916 households are in no campaign at all,
so the strict definition would have survived — the error would never have
surfaced, and every control group would have been silently and unnecessarily
narrowed from ~2,000 households to 916. The estimate would have been noisier for
no methodological gain, and nothing would have indicated why.

---

## 2. Twenty-one viable campaigns were actually three

**The decision.** Which campaigns can support an honest causal estimate.

**What was wrong.** The first real run reported 21 of 30 campaigns as viable. The
screen checked pre-period depth, post-period depth, treatment size and control
size. It also *computed* a contamination figure — the share of treated
households simultaneously enrolled in another overlapping campaign — and then
did nothing with it.

The numbers it was ignoring:

| Campaign | Enrolled | Contaminated | Verdict as first reported |
|---|---:|---:|---|
| 18 | 1,133 | 90.8% | viable |
| 17 | 202 | 100.0% | viable |
| 22 | 276 | 98.2% | viable |

Campaign 17 was marked viable with **every single treated household**
simultaneously enrolled in something else. An estimate on that group does not
measure campaign 17. It measures campaign 17 bundled with whatever else those
households were receiving, and reports the total as if it were the part.

**The correction.** Contamination is not treated as a rejection criterion —
that would discard usable data. Instead, contaminated households are removed
from the treatment group, and the size screen is applied to what remains, since
that is the group the estimator would actually use.

Viable campaigns fell from 21 to 3:

| Campaign | Type | Enrolled | Uncontaminated | Contamination | Control |
|---|---|---:|---:|---:|---:|
| **26** | TypeA | 332 | **310** | **6.6%** | 2,165 |
| 8 | TypeA | 1,076 | 432 | 59.9% | 1,202 |
| 18 | TypeA | 1,133 | 104 | 90.8% | 987 |

Ranking also changed from raw enrolment to contamination, because enrolment is
the misleading number here. Campaign 18 is the largest campaign in the dataset
and among the worst candidates; campaign 26 is a third its size and by far the
best.

**Why it matters.** Sorting by enrolment — the obvious choice — puts the single
worst campaign at the top of the list. A reasonable person builds Phase 6c on
campaign 18, gets a clean-looking estimate with a large sample, and reports an
effect that is mostly other campaigns.

---

## 3. The calendar anchor contradicted 694,450 rows

**The decision.** dunnhumby records time as `DAY` 1–711 relative to an
unpublished panel start. `dim_date` needs real calendar dates, so the panel has
to be anchored somewhere.

**What was wrong.** The plan was to anchor `DAY 1` to a Monday, so that derived
weeks would run Monday–Sunday like retail weeks do. This was stated in the
README before the data was examined, with the reasoning that the choice was
arbitrary and any Monday would do.

It was not arbitrary. dunnhumby ships its own `WEEK_NO` column, and
`causal_data` — 36.8 million rows of promotional exposure — is keyed on it. Any
anchor that disagrees with `WEEK_NO` silently misaligns every join between
transactions and promotions.

The probe tested whether `WEEK_NO` was derivable from `DAY`, assuming
`ceil(day / 7)`. **694,450 of 2,595,732 transactions disagreed** — 26.8%.

**The correction.** Inspecting the actual day span of each week showed the panel
does not begin on a week boundary:

| week_no | day range | days |
|---:|---|---:|
| 1 | 1–5 | **5** |
| 2 | 6–12 | 7 |
| 3 | 13–19 | 7 |

Week 1 is a partial five-day week. The exact rule is `week_no = (day + 8) // 7`,
which matches on all 2,595,732 rows with zero exceptions.

So day 6 opens the first full week and is the Monday anchor, which makes
**day 1 a Wednesday**. The original plan was off by two days.

**Why it matters.** This is the most dangerous error in this document, because nothing
would have failed. Anchoring day 1 to a Monday produces a `dim_date` that looks
entirely correct — 711 consecutive dates, sensible weeks, valid months. Joins to
`causal_data` would succeed and return rows. They would just be the wrong rows,
shifted by two days, for every promotional analysis in the project.

Because of this, a load-time assertion recomputes `week_no` from `day` on every
transaction row and fails the load if a single row disagrees. The rule is now
enforced rather than assumed.

---

## 4. The uniqueness check was run on one table and not the other

**The decision.** Which columns form the natural key of each fact table, and
whether a surrogate key is needed.

**What was wrong.** The probe checked `(basket_id, product_id)` uniqueness on
`transaction_data`, found zero duplicates, and concluded the natural key was
safe. It never ran the equivalent check on `causal_data`.

`causal_data` turned out to contain **15,245 duplicate
`(week_no, product_id, store_id)` rows** out of 36.8M. This surfaced during the
Phase 1 load, not during profiling — the load's `DISTINCT ON` collapsed them,
and the row count mismatch is what exposed it.

**Why it matters.** The consequence was not a crash. `fact_causal` declares that
triple as its primary key, so the duplicates would have been rejected at insert
time with a constraint violation, mid-load, after the expensive part had already
run. Worse, the reconciliation step was written to compare loaded rows against
raw file line counts — which now permanently disagree by 15,245 for a legitimate
reason. A reconciliation that reports a false failure on a documented condition
is one people learn to ignore.

Both are now handled: the duplicate count is measured at load and recorded in
`etl_data_quality`, and reconciliation subtracts it rather than comparing raw
line counts.

The generalisable error is narrower than "we missed a duplicate check". It is
that a screen was applied to one instance of a category and not the others. The
probe had the right check and ran it in one place.

---

## 5. Profiling money in float32 gave the wrong anomaly counts

**The decision.** How many rows carry each known money anomaly — positive
`retail_disc`, negative gross value — since these seed the Phase 4 assertion
suite.

**What was wrong.** The probe cast `sales_value` and `retail_disc` to `float32`
to keep 2.6M rows small in memory. Float32 holds roughly 7 significant digits,
which is enough to display a price correctly and not enough to compare one
against zero reliably.

Re-running the same checks against `NUMERIC(10,2)` after load:

| Check | float32 | NUMERIC(10,2) |
|---|---:|---:|
| `retail_disc > 0` | 36 | **10** |
| `sales_value - retail_disc < 0` | 17 | **1** |
| `sales_value = 0` | 18,850 | **18,879** |

Integer columns were unaffected — `quantity <= 0` and `quantity > 1000` matched
exactly — which is what localises the cause to floating-point representation
rather than to a logic difference between pandas and SQL.

**Why it matters.** These counts were about to become assertion thresholds. A
Phase 4 check asserting "exactly 17 rows have negative gross value" would fail
against correctly loaded data, and the natural response to a failing assertion
is to adjust the threshold — which would have encoded the float32 artifact
permanently.

It also retroactively justifies a decision made for a different reason.
`NUMERIC(10,2)` was chosen for the schema because binary floating point does not
sum money exactly; it turned out to matter for *comparing* money too, one
analysis stage earlier than anticipated.

---

## 6. A number was inferred instead of measured, inside the document about measuring

**The decision.** What to report as the maximum batch duration in the original
load, once the standby-affected batches were set aside.

**What was wrong.** The results table in `docs/performance.md` claimed the
maximum excluding standby was **15.9s**. Nobody measured it. The median was
14.1s, the standby batches were obviously separate, so a value just above the
median looked right and was written down.

The measured value is **31.4s** — roughly double.

**How it was caught.** By running the query before committing, as a final check.
The same query also returned the maximum *before* excluding all known stalls as
88.1s, which is how `w58` was found — so the fourth standby batch and the
invented figure were caught by the same act of checking a number that had
already been written as though it were known.

**Why it matters.** This is the smallest error in this document and the most
uncomfortable, because of where it happened. It appeared in a table inside a
section arguing that benchmarks must report measured values, in a project whose
stated first rule is that no metric may be invented.

That is the actual lesson. The rule was not forgotten, disputed, or overridden —
it was simply not applied at a moment when a plausible value was available and
the cost of checking felt trivial. A wrong number that *looks* wrong gets
queried. 15.9s looked entirely reasonable next to a 14.1s median, which is
precisely why it survived to be written down.

The failure mode is inference filling a gap where a query belonged. It leaves no
trace in the output, because the output is a plausible number in a well-formed
table.

The practical guard is mechanical rather than attitudinal: every figure in a
document must be traceable to a command that produced it. In this project the
load and profile numbers come from `etl_load_control`, `etl_batch_log` and
`etl_data_quality`, which is what made this one checkable at all — the value had
a source, and the source disagreed.

---

## 7. The mechanism verifier reported a 130,537× misestimate that was 1×

**The decision.** Whether each Phase 2 benchmark query is expensive for the
reason its header claims. Q4 predicted a row-estimate failure caused by
correlated columns, and a checker was written to confirm it from the recorded
plan.

**What was wrong — twice.**

First, the checker reported a **130,537× misestimate** at an index scan on
`dim_product`. Postgres reports both `Plan Rows` and `Actual Rows` **per loop**.
The checker multiplied actual rows by `Actual Loops` without doing the same to
the estimate. An index scan estimated at 1 row per loop, delivering exactly 1
row per loop across 130,537 loops, is a *perfect* estimate. It was reported as
the worst planning failure in the plan.

Corrected to compare per-loop against per-loop, it then reported **18.1×** at a
Sort node. Also wrong: the query ends in `LIMIT 200`, which stops the sort early,
so its actual output is truncated by design. A correct estimate looks like a
large overestimate whenever a `LIMIT` sits above it. Row-estimate error is only
meaningful on nodes that *choose* something from the estimate — scans and joins
— so Sort, Aggregate, Gather and Limit are now excluded.

**The result after both fixes.** Q4's largest genuine misestimate is **5.9×**, an
underestimate at a Nested Loop, with the `fact_causal` hash join at 4.0×. Both
are real and both are below the 10× threshold set before the query was written.

**Q4 therefore does not match its claimed mechanism**, and is reported as a
mismatch rather than adjusted until it matches.

**Why it matters.** Each wrong version produced a confident **MATCH** with
specific-looking evidence attached. "130,537×" is not a subtle error — but it
appeared in a generated evidence string next to a green verdict, in exactly the
format a reader trusts. The only reason it was caught is that the number was
absurd enough to prompt reading the plan directly.

The second error is the more dangerous of the two, because 18.1× is *plausible*.
It clears the 10× bar, it sits on a real node, and nothing about it invites a
second look. It would have been published as a confirmed mechanism, and the
subsequent "fix" would have been extended statistics applied to a problem that
was not there — followed by a confusing result where the statistics changed
nothing.

The pattern is error 6's, one level up: not an invented figure this time, but an
invented *metric* — a computation that looked like a measurement and encoded a
misunderstanding of what the source data meant. A figure can be checked against
its source. A metric has to be checked against its definition, and there is no
row to compare it to.

---

## 8. A Decimal was read as a category, and the headline estimate was 5.5x wrong

**The decision.** Which household covariates to adjust the difference-in-differences
estimate for, and how to enter each one into the model.

**What was wrong.** The adjusted estimate for campaign 26 was reported as
**+2.5039**. The correct value is **+0.4510**.

Postgres `NUMERIC` columns arrive in pandas as `Decimal` objects, and pandas
types a column of `Decimal` as `object` — the same dtype it gives a column of
strings. The model-building code decided how to enter each covariate with:

```python
f"C({c})" if merged[c].dtype == object else c
```

So `pre_spend`, a continuous currency column with **2,430 distinct values**, was
wrapped in `C()` and expanded into a 2,430-column categorical design matrix. The
fit ran for **341 seconds** and produced an estimate 5.5x the correct one.

Nothing errored. statsmodels was asked to fit a valid model and did.

**The correction.** Numeric-convertible columns are coerced to float before the
dtype test, categoricals are capped at 50 levels, and — the actual guard —
`assert_model_dtypes()` now runs before every fit and **raises** on an object
column that should be numeric. Types are asserted, not inferred. The fit
dropped from 341 seconds to 0.14.

**Why it matters, and why it is in this document twice over.**

This is the same failure as error 5: not a missing check, but *a correct
operation applied at the wrong type, silently*. Error 5 was money profiled in
float32, where 7 significant digits put values near zero on the wrong side of a
comparison. This is money loaded as Decimal, where the dtype that carries exact
precision is indistinguishable from the dtype that carries text.

Both times the type system was technically satisfied. Both times the wrong
answer looked entirely normal.

**And this one had already been reported as a result.** The +2.5039 figure was
given to the project owner as the adjusted causal estimate for campaign 26,
alongside the naive and unadjusted numbers, before the bug was found. It was not
flagged as provisional. It was wrong by a factor of 5.5.

That is the argument for validating an estimator against synthetic data with a
known true effect, and for re-running that validation whenever the estimator
changes. A wrong estimate on real data is invisible — there is nothing to
compare it against. On synthetic data with a planted effect there is.

**Recovery accuracy after the fix**, on synthetic panels with known effects:

| Scenario | True | Estimated | Abs error | CI covers truth |
|---|---:|---:|---:|---|
| clean, effect 5.0 | 5.0 | 5.285 | 0.285 | yes |
| clean, no effect | 0.0 | 0.285 | 0.285 | yes |
| clean, effect 20.0 | 20.0 | 20.285 | 0.285 | yes |
| high noise | 5.0 | 5.713 | 0.713 | yes |
| large level offset | 5.0 | 5.285 | 0.285 | yes |
| **violated parallel trends** | 5.0 | **12.085** | **7.085** | **no** |

Maximum absolute error across clean scenarios is 0.285, and the confidence
interval covers the truth in 8 of 9 scenarios. The one miss is the scenario
designed to fail: when parallel trends is violated the estimator recovers 12.085
against a true 5.0, a 141.7% error — and the assumption check correctly reports
the violation. An estimator that passed every scenario would not be under test.

---

## What generalises

All eight errors share a shape: each produced output that looked correct.
Nothing crashed, nothing was empty, no test failed. The strict control group
returned a plausible number, the 21 viable campaigns were a plausible finding, a
Monday-anchored calendar is a plausible calendar, 36.8M rows is a plausible row
count, and 17 is a plausible number of anomalies.

Four things caught them, and none was code review:

- **A synthetic dataset with deliberately seeded defects**, built because the
  real data had not arrived yet. It was constructed to include a blanket
  campaign, and that is the only reason the control-group flaw surfaced at all.
- **Checking a derived value against its source rather than trusting the
  derivation.** `week_no` was recomputed and compared. Had it merely been
  derived and used, the mismatch would never have been visible.
- **Recomputing the same measurement in a second system.** The float32 anomaly
  counts looked fine until SQL disagreed with pandas. Neither was obviously
  wrong on its own; the disagreement is what was informative.
- **Reconciling totals rather than assuming them.** The 15,245 duplicates were
  found because loaded rows were compared against source lines and the numbers
  did not match — not because anyone suspected duplicates.

All four are now permanent: the synthetic fixtures back the test suite, the
`week_no` check runs on every load, quality counts are measured in SQL against
exact decimals and recorded in `etl_data_quality`, and reconciliation runs as a
load step with an explicit, recorded allowance for deduplication.

The remaining pattern worth naming is the one behind errors 4 and 5: both came
from a check that was *correct* and applied *incompletely* — the right
uniqueness test run on one of two fact sources, and the right anomaly queries
run at the wrong precision. Neither was a missing idea. Both were a good idea
applied to part of its domain, which is harder to notice than an absent check
and is why they survived into the load.
