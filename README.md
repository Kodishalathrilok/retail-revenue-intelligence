# Retail Revenue Intelligence Platform

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
| 5–8 | not started | API, AI layer, frontend |

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
```

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
