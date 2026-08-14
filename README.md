# Retail Revenue Intelligence Platform

**Repository:** https://github.com/Kodishalathrilok/retail-revenue-intelligence

> **Deployment status: not yet live.** The aggregate tier is built and measured
> (14.6 MB, 120,800 rows across 23 tables) and the hosting plan is in
> [`docs/deployment.md`](docs/deployment.md), but nothing is deployed — see
> *What runs where* below for exactly what a hosted visitor would and would not
> be able to do.

Analytics platform over the dunnhumby *Complete Journey* household panel:
2,595,732 transactions and 36,771,279 rows of promotional exposure across 2,500
households and 711 days, in PostgreSQL 16.

Three questions, three engines, one rule — **the model interprets, deterministic
systems calculate**:

```
DESCRIPTIVE   what happened?              -> validated NL->SQL
PREDICTIVE    what happens next?          -> a fitted forecaster, scored on a
                                             locked temporal test set
CAUSAL        did the campaign cause it?  -> difference-in-differences
```

## Three things the data actually says

**Revenue is far less concentrated than the 80/20 rule predicts.** Reaching 80%
of revenue takes **11,865 products — 12.9% of the catalogue**, not 20%. The top
1% of products carries 37.7%. Half of all revenue comes from just 2,295 products
(2.5%). The tail is long and it matters: a range review that cut the bottom 80%
of the catalogue would be cutting products that collectively earn a fifth of
revenue.

**One in five households generates almost half of revenue.** RFM segmentation
puts 512 households (20.5% of the panel) in *Champions* — recent, frequent, high
value — and they account for **45.6% of all revenue**. A further 116 households
(4.6%) are high-value but lapsing, worth 7.3% of revenue between them. That
second group is the actionable one: small enough to target individually, large
enough to matter.

**The strongest product affinities are cross-category, not within-category.**
The highest lift pair in the data is `CEREAL/BREAKFAST` with `FROZEN` at
**24.3× more often than chance** — and `FROZEN` appears in three of the top five
pairs, coupling with cereal, refrigerated goods and snacks. The intuitive
within-category pairs rank lower. `BABY FOODS` with `INFANT CARE` (18.0×) is the
exception that shows the measure is working: a genuine shopping-mission pair.

Full analyses in [`sql/analytics/`](sql/analytics/), one documented query per
business question.

## A note on what this data can and cannot support

dunnhumby is a **panel**: households are recruited at the start of observation
rather than acquired over time. **99.8% make their first purchase within the
first 180 days** of a 711-day window — 70.5% within 90 days, median day 69 — and
only 6 households of 2,500 first appear after day 180.

Calendar-month cohorts therefore crowd into the first six months, and a "cohort
comparison" would contrast early recruits against a handful of stragglers whose
late first purchase reflects low shopping frequency, not late acquisition. The
retention analysis uses **relative tenure** — each household on its own timeline
from its first purchase — which measures how engagement decays with time
observed and makes no acquisition claim. The output carries that basis as a
column so it travels with the numbers.

**On dates:** the source records `DAY` 1–711 with no published calendar anchor.
`WEEK_NO` follows `(DAY + 8) / 7` exactly, so week 1 is a partial five-day week
and day 6 opens the first full week — making day 6 the Monday anchor and day 1 a
Wednesday. Elapsed intervals, month boundaries and year-over-year comparisons
are real. **Weekday labels are a modelling convention**, and this project makes
no day-of-week claims.

## What is built

| Phase | Status | Deliverable |
|---|---|---|
| 0 — dataset probe | done | [`docs/dataset-profile.md`](docs/dataset-profile.md) |
| 1 — schema and load | done | [`docs/schema.md`](docs/schema.md), resumable loader |
| 2 — query performance | done | [`docs/performance.md`](docs/performance.md) |
| 3 — analytical SQL | done | [`sql/analytics/`](sql/analytics/) — 8 queries |
| 4 — data quality | done | 29 assertions, CLI, CI-wired |
| 5 — API | done | FastAPI, pooled async, keyset pagination |
| 6 — AI layer | done | Validated NL→SQL, grounded narration, causal DiD |
| 7 — frontend | done | Next.js 14 + Recharts, four views |
| 8 — docs and deploy | done | [`docs/deployment.md`](docs/deployment.md) |
| 9 — predictive | done | [`reports/eval/forecast_release.md`](reports/eval/forecast_release.md) — and the ML model did **not** ship |

### The AI layer, and the rule it is built around

**The model is never the computational authority.** SQL and Python compute; the
model proposes SQL, explains computed results, and suggests confounders. Every
figure that reaches a user was produced by Postgres or statsmodels.

The model does emit arithmetic — `SUM(x) / COUNT(*)` is arithmetic — but it is
the database that evaluates it, against real rows, where the result is
reproducible by anyone who runs the same query. The claim is about *who is
trusted to be right*, not about whether an operator appears in the output.

That is enforced structurally, not by prompting:

- **Answerability routing runs first**, before any model call
  ([`src/rrip/ai/router.py`](src/rrip/ai/router.py)). A question is classified
  `ANSWERABLE`, `AMBIGUOUS`, `UNSUPPORTED`, `UNSAFE` or `TOO_EXPENSIVE` from
  vocabulary declared in the semantic layer — deterministically, with no second
  LLM call, because asking the untrusted component to judge its own competence
  is not a check. Its cost is measured too: **0 of 31** questions with a known
  reference answer were blocked.
- **NL→SQL** then passes six gates — single `SELECT` with no recursion,
  forbidden-keyword scan against SQL stripped of comments and string literals,
  function allowlist plus a catalog ban, `EXPLAIN` cost ceiling, guarded
  execution, result shape — with rejections fed back for up to two retries.
  Every gate result is returned and rendered.
- **The gates are not the boundary.** They are a program reasoning about SQL
  text, and this one had a real bypass: `query_to_xml(…)` executes its string
  argument, and the keyword gate strips string literals before scanning,
  precisely so that `WHERE name = 'drop table'` is not a false reject.
  `EXPLAIN` missed it too, because `EXPLAIN` without `ANALYZE` never runs a
  function body. The boundary is the database role:
  [`sql/ddl/60_readonly_role.sql`](sql/ddl/60_readonly_role.sql) holds `SELECT`
  and nothing else.

  What that role actually does was then *verified rather than asserted*, and the
  first verification corrected this README. `query_to_xml('DELETE …')` does not
  run under the role — but it does not run for a **superuser** either
  (`SQLSTATE 0A000`: the function is `STABLE`, so Postgres refuses a `DELETE`
  inside it). The role is not what stops that one. The `SELECT` form *does*
  execute, as the calling role, and is refused on `pg_authid` with `42501`. So
  the bypass is real, it is a **read** bypass, and the role is what bounds its
  reach. `rrip verify-role` runs 15 probes as `rrip_ro`; **13 of the refusals
  come from PostgreSQL itself**, and the probes use raw SQL that never touches
  the gates, so application and database rejection are never confused.
- **Narration** receives a computed result set with the arithmetic already
  done — absolute changes, percentages and shares are computed by
  [`src/rrip/ai/derive.py`](src/rrip/ai/derive.py) and supplied as named
  fields, so the model explains numbers rather than producing them. Any number
  in its response that is absent from that input causes the whole response to
  be **rejected, not repaired**. Measured below.
- **Causal** lets the model propose confounders and nothing else. Proposals
  outside the schema are discarded. Estimation is statsmodels.
- **Forecasting** is routed to before SQL is ever generated. This is the
  sharpest case for the rule: asked "what will Grocery revenue be next week?"
  with only a SQL tool available, a model does not refuse — it writes a valid
  aggregate over historical rows and returns a number that passes every gate
  here and is not a forecast. A deterministic `FORECAST` verdict sends the
  question to a fitted predictor instead, and the model's entire remaining job
  is picking a department name from a closed list, validated against that list
  before use. Predictive questions about metrics that were never modelled —
  units, baskets, households — are refused rather than answered with the
  nearest available series.

### NL→SQL, measured rather than asserted

52 benchmark questions for the local tier, graded by **executing** the generated
SQL and comparing its result set against a reference query — not by comparing
SQL text, which measures phrasing. Every question is also run with the router
disabled, so the router's effect is a measurement rather than a claim.

`rrip eval` / `rrip eval --no-router`, gemini-flash-lite-latest, 2 attempts:

| | router off | router on |
|---|---:|---:|
| **Result equivalence** vs reference (31 graded) — *the correctness measure* | 93.5% | **93.5%** |
| Final execution success — *SQL ran; not correctness* | 98.1% | 63.5% |
| First-attempt execution success — *not correctness* | 88.5% | 61.5% |
| Unanswerable questions refused | 100% | **100%** |
| Ambiguous questions clarified rather than guessed | 0% | **100%** |
| **Harmful SQL executed** (12 adversarial/expensive) | **0** | **0** |
| Router false positives (answerable questions blocked) | — | **0 / 31** |

Executable rate and first-attempt success *fall* with the router on, and that is
the router working: a question it declines never reaches SQL generation, so it
cannot be counted as executing. Reporting only the number that went up would be
the dishonest version of this table.

**What actually fixed the unanswerable questions was the semantic layer, not the
router.** An earlier run scored 25% there — asked "how much did we spend on
advertising?", a dataset with no cost data at all, the system produced
`WHERE p.department = 'ADVERTISING'` and labelled retail revenue as advertising
spend. Declaring the absent subjects once in
[`src/rrip/semantic/`](src/rrip/semantic/definitions.py) and generating the
prompt from them took that to 100% **with the router still switched off**. The
router's measured contribution is the ambiguity column and determinism: those
refusals now cost no model call and cannot vary between runs.

Two failures remain, and both are **defects in the benchmark rather than the
model**:

| case | what happened |
|---|---|
| `grp-03` | "How many households are in each income bracket?" — the reference counts households with no recorded bracket as a bracket (13 groups); the model excluded them (12). Both readings are defensible and the question does not say. |
| `win-02` | "Rank the departments by revenue." — filed under `window` to exercise `rank() OVER`, but the question never asks for a rank column. The model returned departments ordered by revenue, which answers what was asked. |

They are left **failing on purpose**. Sharpening a question after seeing the
answer is how a benchmark stops measuring anything, so they stay as recorded
failures until the convention is decided and applied to both sides.

Latency is reported in [`reports/eval/latest.md`](reports/eval/latest.md) with
the caveat that the provider caches responses, so a re-run measures the cache.
`RRIP_LLM_CACHE=0 rrip eval` measures the model.

### The semantic layer

Metric definitions used to live as prose in the NL→SQL prompt, and the same rule
was restated in the published-tier prompt, in the docs, and in every reference
query in the benchmark. Four copies drift. They now live once in
[`src/rrip/semantic/definitions.py`](src/rrip/semantic/definitions.py) — metric
expression, grain, synonyms, caveats, plus the subjects this dataset has **no
data for** and the question shapes that are under-specified — and the prompt,
the router, the evaluator and the docs are generated from them.

It is Python rather than YAML on purpose: the core dependency set is 30 MB
against a 250 MB serverless limit, and adding a YAML parser to the deployed path
to express a static dict is a real cost for no gain.

### Narration grounding, and the claim that did not survive its own audit

**There is no measured improvement from structured payloads, and this section
used to claim one.** The A/B — derived fields against raw rows — is withdrawn.
An adjudication pass
([`eval/narration/adjudication_report.md`](eval/narration/adjudication_report.md))
found that one of the three discordant pairs the result rested on, `NAR-092`,
was a **confirmed mis-grade**: the baseline narration correctly refused to
compute growth from an all-zero column, and the classifier scored it
`SEMANTICALLY_WRONG` because `NEGATION_MARKERS` contains `'no cost'` but no bare
`'no '`.

Removing that row leaves **2 discordant pairs, exact McNemar p = 0.5** — and
0.5 is the *smallest p attainable* at n = 2. The design cannot produce evidence
at this sample size, so no amount of consistent direction rescues it. The
per-arm faithfulness percentages are not restated here either: recomputing them
means re-running the classifier with the defect fixed, and that fix is a
separate commit.

**What the benchmark does support is architectural, not comparative.** Every
figure the narration layer describes — absolute changes, percentages, shares —
is computed by [`src/rrip/ai/derive.py`](src/rrip/ai/derive.py) and supplied as
a named field. The model's output is then checked against that input, and any
number absent from it causes the whole response to be rejected rather than
repaired. That is a property of the wiring, verifiable by reading it, and it
does not depend on a p-value.

The measurement that stands is the observed rate, stated with its uncertainty
rather than as a bare zero:

> **0 of 65 cases produced an unsupported numeric claim** on either path.
> With no events in 65 trials the one-sided 95% upper bound is **4.5%**
> (Clopper–Pearson; the familiar rule-of-three approximation gives 3/65 ≈ 4.6%).

So the honest ceiling is "below roughly 5%", not "zero". 65 cases cannot
demonstrate a rate lower than that, and reporting 0.0% invites the reader to
believe otherwise.

The 65 cases
([`eval/datasets/narration_v1.jsonl`](eval/datasets/narration_v1.jsonl)) span 19
categories — percentages, zero denominators, NULLs, rounding, rankings,
unsupported metrics, and cases built to tempt arithmetic. Grading is mechanical:
traps, expected direction, expected winner and forbidden claims, all written
before any model call. No LLM judge. Full breakdown in
[`reports/eval/latest.md`](reports/eval/latest.md); the audit that withdrew the
A/B, including two defects it found in its own judge, is in
[`eval/narration/adjudication_report.md`](eval/narration/adjudication_report.md).

### Forecasting: the ML model lost to a trailing mean, and did not ship

The third leg of descriptive → predictive → causal. One week ahead, weekly
revenue for each of 23 departments, evaluated on a temporal test set (weeks
88–101) that was locked during model selection.

**Read this before the table.** The deployed predictor is **not the best
predictor on the test set**. It ranks **4th of the 8 predictors scored** there
(3rd of the 6 registered baselines). It was chosen on *validation*, and it is
left in place deliberately:

- **Reselecting on test would burn the only untouched measurement in this
  project.** Picking whichever candidate scored best on weeks 88–101 converts
  that number from an unbiased estimate into a selection statistic, and there
  is no second held-out set to recover one from.
- **The gaps are not resolvable anyway.** The deployed baseline is
  statistically indistinguishable from the two trailing-window baselines above
  it — Diebold-Mariano p = 0.26 against the 8-week mean and p = 0.17 against
  the 8-week median.
- **There is direct evidence those gaps are noise.** The ordering of the three
  trailing-window baselines is *exactly reversed* between validation and test:

  | rank | validation (rolling-origin) | test (weeks 88–101) |
  |---|---|---|
  | 1 | `trailing_mean_4` **(deployed)** | `trailing_median_8` |
  | 2 | `trailing_mean_8` | `trailing_mean_8` |
  | 3 | `trailing_median_8` | `trailing_mean_4` **(deployed)** |

  `seasonal_naive_52` makes the same point from the other direction: it was
  **rejected** on a weeks 28–101 measurement (8.72% WAPE against 7.93% for an
  8-week trailing mean) and comes **first** on the test weeks at 8.85%. A
  ranking that inverts between two windows of the same panel is measuring the
  window, not the predictor.

So the honest reading is that these four predictors are one predictor with four
spellings, and the choice among them is not a result. That is the finding, and
it is the strongest part of this module — stronger than any of the accuracy
numbers below.

**The headline is a negative result.** A tuned gradient-boosting model beat
every weak baseline and was indistinguishable from every strong one:

| predictor | MAE | RMSE | WAPE |
|---|---:|---:|---:|
| seasonal naive, lag 52 | 356.2 | 1,089.9 | 8.85% |
| trailing median, 8wk | 373.4 | 978.8 | 9.28% |
| trailing mean, 8wk | 378.4 | 971.6 | 9.40% |
| **trailing mean, 4wk — deployed** | **396.5** | **1,035.3** | **9.85%** |
| gradient boosting (challenger) | 396.7 | 1,017.1 | 9.86% |
| seasonal naive, lag 4 | 483.9 | 1,204.2 | 12.02% |
| naive | 487.2 | 1,402.6 | 12.11% |
| drift | 507.4 | 1,451.2 | 12.61% |

Diebold-Mariano on absolute-error loss: the model beats **naive** (p = 0.030),
**seasonal-naive-4** (p = 0.005) and **drift** (p = 0.012), and is
indistinguishable from all three trailing-window baselines (p = 0.99, 0.26,
0.17) — nominally *worse* than two of them. It is closer than the deployed
baseline on 47.5% of test rows: a coin flip.

So a promotion rule fixed **before the test set was unlocked** — beat the
baseline *and* have the difference be distinguishable from zero — deployed the
baseline. The model, its metadata and the whole benchmark stay in the repo as
the evidence for that decision rather than as a deleted branch.

**Four independent findings say the same thing**, which is why this reads as a
property of the data rather than a failed experiment:

- Post-ramp lag-1 autocorrelation of weekly revenue is **+0.07**, so last
  week's figure barely predicts next week's. This is also why naive is a weak
  baseline and why quoting an improvement over it would be dishonest.
- Running the forecast on **one-week-stale data is marginally better**
  (−0.51pp WAPE). The most recent week carries no usable signal.
- An oracle variant given **next week's promotions is worse** (−2.4%).
  Department-week promo aggregates over 92,353 products carry nothing.
- A **52-week lag performs as well as anything**, which is what a series with
  little exploitable structure looks like.

Department weekly revenue in this panel is, to the accuracy 322 test
observations can measure, **a local level plus noise**. Estimating the level is
the whole job, and a trailing mean estimates it.

**The leakage audit earned its place on its first run.** It failed
`target_independence` and `future_window` on one feature, and the cause was
real: `groupby(...)[col].shift(1).rolling(8)` reads correctly and is not
grouped — `SeriesGroupBy.shift` returns a plain Series, so the rolling ran
across department boundaries. Mean and median were unaffected in the delivered
rows; the **variance** was affected everywhere, because pandas computes rolling
variance with an add/remove accumulator that carries rounding error from values
that have already left the window. Both are now permanent regression tests.

Other measured results: **prediction intervals** are split-conformal on
scale-normalised residuals, and realised coverage is 80.8% against 80% nominal
and 94.7% against 95%. An earlier configuration that calibrated on the
challenger's residuals but served the baseline's forecasts under-covered at
77.0% and 91.9% — an interval fitted to one predictor and wrapped around
another's output. **Pooled WAPE 9.85% against macro WAPE 31.6%** is the gap
between the dollar-weighted headline and the unweighted truth: GROCERY is 51.6%
of revenue and carries 40.8% of all error. Sparse departments score 44% WAPE
and are flagged `LOW`; a department whose trailing window is empty is refused
outright rather than extrapolated.

`GET /api/v1/forecast` serves precomputed rows with the standard library alone
— no numpy, no scikit-learn — which is what keeps it inside the 250 MB
serverless limit. Full write-up, including every failure, in
[`reports/eval/forecast_release.md`](reports/eval/forecast_release.md).

### Causal estimator validation

Before believing anything the DiD code says about a real campaign, it is asked
to recover effects it was given. `rrip causal-validate`, 100 simulations on
synthetic panels with a planted effect of 5.0:

| | |
|---|---:|
| Mean estimate | 5.024 |
| Bias | +0.024 |
| 95% CI coverage | **95.0%** (nominal 95%) |

Coverage materially below nominal would mean the intervals are too narrow — the
estimator claiming more certainty than it has. Nine single-run scenarios are
also reported, including two that are **supposed to fail**: with parallel trends
deliberately violated the estimator returns 12.09 against a true 5.0 and its
interval excludes the truth, which is the point. An estimator that passes every
scenario has not been tested.

### Causal result, measured

Difference-in-differences on campaign 26 (310 uncontaminated treated, 2,140
control, parallel trends hold at p = 0.39):

| | Estimate | 95% CI | p |
|---|---:|---:|---:|
| Naive before/after, treated only | **+6.53** | — | — |
| Difference-in-differences | **+1.51** | [−2.35, +5.36] | 0.44 |
| Adjusted for confounders | +0.45 | | |

Standard errors are clustered by household (SE 1.97); the interval is what the
p-value alone hides. **Not significant is not the same as no effect** — this
data is consistent with anything from a $2.35 decrease to a $5.36 increase, and
the honest reading is that the campaign's effect is not measurable at this
sample size, not that it is zero.

**The naive number is 4.3× the DiD estimate.** A dashboard reporting
before/after on the treated group alone would report an effect roughly four
times larger than the one that survives comparison with a control group. Which
of the two is closer to the truth depends on the DiD assumptions holding — the
parallel-trends test below is what makes that checkable rather than asserted.

Campaign 18 is retained as a contaminated counter-example: 90.8% of its enrolled
households were simultaneously in an overlapping campaign, parallel trends are
violated at p = 0.012, and the verdict is **NOT CREDIBLE**. It is also the
largest campaign in the dataset — sorting by enrolment puts the worst candidate
first.

### Performance work, honestly reported

[`docs/performance.md`](docs/performance.md) records five optimizations, of which
**one clearly worked, one was marginal, and three did not move the number** —
each aimed at a mechanism identified from a query plan before the fix was
applied.

It also documents three measurement failures worth more than the optimizations:
a load that was 72% laptop-sleep, a "cold vs warm" distinction that turns out to
be near-meaningless for large sequential scans, and a rewrite that ran 1.35×
faster while doing nothing it claimed.

[`docs/methodology-notes.md`](docs/methodology-notes.md) writes up seven
decisions that were made wrongly first and corrected against evidence.

## Setup

Requires Python 3.12 and PostgreSQL 16 with **C collation**.

```bash
py -3.12 -m venv .venv && ./.venv/Scripts/python.exe -m pip install -e ".[dev]"
```

Copy `.env.example` to `.env` and fill in credentials. `.env` is gitignored.

The raw CSVs sit **outside the repo** — ~1.5 GB, and keeping them out of a
cloud-synced folder avoids Files On-Demand stalling the loader on a file that
appears present. Point `RRIP_DUNNHUMBY_RAW_DIR` wherever they are unzipped;
discovery is recursive and case-insensitive.

```bash
rrip profile     # measure the raw files before loading
rrip load        # resumable star-schema load
rrip reconcile   # loaded rows vs source lines
rrip quality     # 29 assertions; exits non-zero on failure
rrip bench       # Phase 2 benchmark harness
rrip eval        # NL->SQL benchmark; exits non-zero if harmful SQL executed
rrip eval --no-router   # same suite, routing disabled -- the A/B baseline
rrip eval-cases  # list the benchmark questions and how each is graded
rrip causal-validate    # recover known effects; report bias and CI coverage
rrip forecast-train     # leakage audit -> select -> unlock test -> artifact
rrip forecast-eval      # predictive benchmark; exits non-zero on any failure
rrip forecast-report    # write reports/eval/forecast_release.md
rrip verify-role # connect as rrip_ro and attempt every forbidden operation
rrip eval-report # assemble reports/eval/latest.md from measured artefacts
rrip serve       # FastAPI on :8010 (set RRIP_API_PORT to change)
```

### The test suite, and how its size is quoted

```bash
python -m pytest
```

**710 passed**, 7 skipped, 1 failed — as of `pytest 9.1.1`, 2026-08-14.

The convention is **passed-only**: a quoted suite size is the `passed` count
from one full run, never `passed + skipped` and never the collected total.
Those three numbers differ here by eight, which is enough for two documents
quoting different ones to look like a regression.

The skips are visible on purpose — the read-only role probes skip when no
`RRIP_RO_DSN` is configured rather than passing vacuously. The failure is
`test_load_integrity.py::test_rerunning_a_dimension_insert_is_a_noop`, which
needs the `stg_product` staging table that exists only mid-load; it is a
fixture gap, not a defect in the code under test, and it is quoted here rather
than netted out of the headline.

### The read-only role

The API should not connect as the owning role. Create the read-only one once,
after the tables exist, then point the API at it:

```bash
psql -U postgres -d rrip -v ro_password='<choose one>' -f sql/ddl/60_readonly_role.sql
```

Set `RRIP_RO_PASSWORD` (or a full `RRIP_RO_DSN`) in `.env` and run
`rrip verify-role`. It exits non-zero on anything other than `VERIFIED`, and a
role that was never created reports `NOT_DEPLOYED` and fails rather than being
skipped — a verifier that quietly passes when it cannot connect is the failure
it exists to prevent. The same probes run under pytest and skip, visibly, when
no read-only DSN is configured.

```bash
cd frontend && npm install && npm run dev
```

For the AI layer, set `GEMINI_API_KEY` in `.env` (free tier). The provider is
config-switched — Groq is the alternate — and falls back across Gemini models,
because free-tier availability shifts: `gemini-2.0-flash` returns 429 quota
exceeded on a new key and `gemini-2.5-flash` returns 404 for new users, while
`gemini-flash-latest` works.

## What runs where

The full 3.7 GB database does not fit any free hosted tier — `fact_causal` alone
is 3,193 MB. So this splits into two tiers, and **the split changes what a
hosted visitor can actually do.** Stating that here rather than letting someone
find it by hitting a wall:

| | Local (full pipeline) | Hosted (aggregate tier) |
|---|---|---|
| Data | 39.6M rows, 3,713 MB | **120,800 rows, 14.6 MB** (measured) |
| Executive overview | ✅ | ✅ from `pub_weekly_revenue*` |
| Department drill-down | ✅ | ✅ from `pub_weekly_revenue_by_dept` |
| RFM, retention, Pareto, affinity | ✅ | ✅ precomputed |
| Causal DiD | ✅ live estimation | ⚠️ **precomputed results only** — cannot re-run against another campaign or window |
| Forecast | ✅ | ✅ identical numbers — the forecasts are precomputed on **both** tiers, so this one is not a downgrade |
| Forecast *training* | ✅ | ❌ needs scikit-learn and the 36.8M-row panel |
| **NL→SQL** | ✅ full star schema | ⚠️ **aggregate tables only** |
| Phase 2 benchmarks | ✅ | ❌ measurements of a specific machine |
| 39.6M-row load | ✅ | ❌ stays local by design |

**The NL→SQL restriction is the one that matters to a visitor.** In production
the model writes against `pub_*` aggregates and the published dimensions — so
"which 5 departments have the highest revenue?" works, and "which households
bought product X?" does not, because `fact_transactions` is not published. The
validation gates, cost ceiling and retry behaviour are identical in both tiers;
only the schema is narrower.

```bash
rrip publish --local-only   # build and measure the aggregate tier
rrip publish                # push to RRIP_PUBLISH_DSN
```

Publishing is idempotent — each table is dropped and recreated in its own
transaction, verified by running it twice to identical output — and writes a
`pub_manifest` row per table so a stale deployment is detectable rather than
assumed fresh.

## Architecture

PostgreSQL 16 star schema: `fact_transactions` (2.6M rows, monthly partitions)
and `fact_causal` (36.8M rows, 102 weekly partitions) against conformed
dimensions, with bridges for the many-to-many coupon and campaign relationships.

The loader stages via `COPY` into unlogged tables, asserts, then inserts into
partitioned facts in batches — recording per-batch throughput, because that is
what made the standby stalls visible. Every step is resumable and idempotent.

Data quality results are measured at load into `etl_data_quality` and read from
there by both reconciliation and the assertion suite, so no threshold is
hardcoded from a profiling pass that computed money in float32.
