# Retail Revenue Intelligence Platform

**Repository:** https://github.com/Kodishalathrilok/retail-revenue-intelligence

> **Deployment status: not yet live.** The aggregate tier is built and measured
> (11.3 MB, 117,120 rows across 16 tables) and the hosting plan is in
> [`docs/deployment.md`](docs/deployment.md), but nothing is deployed — see
> *What runs where* below for exactly what a hosted visitor would and would not
> be able to do.

Analytics platform over the dunnhumby *Complete Journey* household panel:
2,595,732 transactions and 36,771,279 rows of promotional exposure across 2,500
households and 711 days, in PostgreSQL 16.

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
| 7 — frontend | done | Next.js 14 + Recharts, three views |
| 8 — docs and deploy | done | [`docs/deployment.md`](docs/deployment.md) |

### The AI layer, and the rule it is built around

**The model never computes a number.** SQL and Python compute; the model
proposes SQL, explains computed results, and suggests confounders. Nothing that
reaches a user originates in a language model.

That is enforced structurally, not by prompting:

- **NL→SQL** passes five gates — single `SELECT`, forbidden-keyword scan against
  SQL stripped of comments and string literals, `EXPLAIN` cost ceiling, guarded
  execution, result shape — with rejections fed back for up to two retries.
  Every gate result is returned and rendered.
- **Narration** receives only a computed result set, and any number in its
  response that is absent from that input causes the whole response to be
  **rejected, not repaired**. An adversarial test suite tries to induce
  fabricated figures; it found a real hole in the guard, which is now closed.
- **Causal** lets the model propose confounders and nothing else. Proposals
  outside the schema are discarded. Estimation is statsmodels.

### Causal result, measured

Difference-in-differences on campaign 26 (310 uncontaminated treated, 2,140
control, parallel trends hold at p = 0.39):

| | Estimate |
|---|---:|
| Naive before/after, treated only | **+6.53** |
| Difference-in-differences | **+1.51** (p = 0.44, not distinguishable from zero) |
| Adjusted for confounders | +0.45 |

**The naive number is 4.3× the DiD estimate.** A dashboard reporting
before/after on the treated group alone would claim an effect roughly four times
larger than the data supports — and the DiD estimate is not statistically
distinguishable from zero at all.

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
rrip serve       # FastAPI on :8010 (set RRIP_API_PORT to change)
```

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
| Data | 39.6M rows, 3,713 MB | **117,120 rows, 11.3 MB** (measured) |
| Executive overview | ✅ | ✅ from `pub_weekly_revenue*` |
| Department drill-down | ✅ | ✅ from `pub_weekly_revenue_by_dept` |
| RFM, retention, Pareto, affinity | ✅ | ✅ precomputed |
| Causal DiD | ✅ live estimation | ⚠️ **precomputed results only** — cannot re-run against another campaign or window |
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
