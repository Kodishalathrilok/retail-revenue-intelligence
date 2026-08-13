# Deployment

## The constraint that shapes everything

**The full dataset cannot go to a free hosted tier, and there is no clever way
around that.**

| Object | Size |
|---|---:|
| `fact_causal` (36.8M rows, 102 partitions) | 3,193 MB |
| `fact_transactions` (2.6M rows, 24 partitions) | 483 MB |
| Dimensions, bridges, indexes | ~40 MB |
| **Total** | **3,713 MB** |

Free Postgres tiers offer roughly 0.5 GB (Supabase) to 3 GB (Neon, on generous
terms and subject to change). Even the most generous is at or below the size of
one table, before indexes.

So this deploys as an **aggregate-only tier**. That is a deliberate architecture,
not a degraded version of the real thing, and the README says so plainly rather
than implying a full deployment.

## What runs where

```
LOCAL (unchanged)                         HOSTED (aggregate-only)
─────────────────────────────────         ───────────────────────────────
39.6M-row load                            ~50k rows of computed aggregates
Phase 2 benchmark harness                 FastAPI read-only
Phase 3 analytical SQL                    Next.js frontend
DiD estimation (statsmodels)              NL->SQL against aggregate tables
                                          Pre-computed causal results
        │                                          ▲
        └───── rrip publish ───────────────────────┘
               (push computed aggregates)
```

**Local keeps the heavy pipeline.** Loading, benchmarking, the analytical query
library, and causal estimation all stay on the machine that has the raw files.
Nothing about that changes.

**Hosted gets computed results only.** Everything the dashboard reads is an
aggregate that local already computed.

## What gets published

Roughly 50,000 rows total — about 12 MB, comfortably inside every free tier.

| Table | Rows | Source |
|---|---:|---|
| `pub_weekly_revenue` | 102 | Weekly revenue, cumulative, rolling average |
| `pub_weekly_revenue_by_dept` | ~2,300 | The same, per department, for drill-down |
| `pub_rfm_segments` | 7 | RFM segment aggregates |
| `pub_retention_tenure` | 72 | Relative-tenure retention curves |
| `pub_pareto_products` | ~5,000 | Top products with cumulative revenue share |
| `pub_commodity_affinity` | ~1,000 | Market basket lift pairs |
| `pub_reorder_by_department` | 12 | Reorder rates |
| `pub_promo_exposure` | ~250 | Promotional display rate by department and week |
| `pub_causal_results` | 3 | Pre-computed DiD for campaigns 26, 8, 18 |
| `pub_causal_pretrend` | ~160 | Pre-period series for the parallel-trends plot |
| `dim_date`, `dim_week`, `dim_product`, `dim_store`, `dim_household` | ~96,000 | Dimensions, for NL→SQL to have a real schema |

### What is deliberately NOT published

- **`fact_causal`** — 3.2 GB, and nothing in the dashboard reads it row-by-row.
- **`fact_transactions`** — 483 MB. Excluding it means hosted NL→SQL cannot
  answer arbitrary transaction-level questions. **That limitation must be stated
  in the UI**, not discovered by a user whose question returns nothing.
- **Raw household-level rows** — the panel is licensed for research use;
  publishing per-household transaction data to a public endpoint is a licence
  question, not just a size one.

## Honest consequences

1. **NL→SQL is narrower when hosted.** The gates and retry behaviour are
   identical — that is the feature — but the schema it writes against is
   aggregate tables, so "which households bought X" is unanswerable. The hosted
   UI should say which tables are available.
2. **Causal results are precomputed, not live.** The estimation runs locally and
   the result is published. A hosted user sees the estimate, the confidence
   verdict and the pre-trend plot, but cannot re-run it against a different
   campaign or window.
3. **Phase 2 numbers cannot be reproduced on the hosted tier.** They are
   measurements of a specific machine with 36.8M rows. `docs/performance.md`
   already states its hardware; the hosted deployment does not invalidate it,
   but nor can it demonstrate it.

## Publishing

`rrip publish` (to be built) would:

1. Connect to local and to `RRIP_PUBLISH_DSN`.
2. Create the `pub_*` tables if absent.
3. Recompute each aggregate from the local star schema.
4. `COPY` results out and into the target inside one transaction per table.
5. Run the quality suite against the published tier — the same assertions where
   they apply — and exit non-zero on failure.
6. Record a `pub_manifest` row with source row counts, published row counts and
   a timestamp, so a stale deployment is detectable rather than assumed fresh.

Refresh is manual. The panel ended at day 711; the data does not change.

## What I need from you

| # | What | Where | Why | Notes |
|---|---|---|---|---|
| 1 | **Neon account + connection string** | [neon.tech](https://neon.tech) | Hosted Postgres for the `pub_*` tables | Free tier. Create the project with **C collation** if the option is offered, so ordering matches local. Put the DSN in `.env` as `RRIP_PUBLISH_DSN`. |
| 2 | **Vercel account** | [vercel.com](https://vercel.com) | Next.js frontend hosting | Free Hobby tier. Connect it to the GitHub repo; set root directory to `frontend/`. |
| 3 | **A host for the FastAPI service** | Cloud Run, Render or Fly.io | The API is Python, so Vercel cannot host it | **Fly.io has no free tier** -- it was removed in 2024, and new accounts get a 2-hour trial then need a card (~$2-5/month). See the comparison below. |
| 4 | **Gemini API key for the hosted env** | already have | NL→SQL and narration | Set as an environment variable on the API host — **not** committed. The rotated key is fine. |
| 5 | **GitHub repository** | — | CI already exists and needs somewhere to run | Currently local-only, no remote. |

**Not needed:** any domain, any Anthropic key.

### Choosing an API host

Verified August 2026. **Fly.io no longer has a free tier** -- it ended in 2024,
and the earlier version of this document was wrong to call it free.

| Host | Free? | Cold start | Notes |
|---|---|---|---|
| **Google Cloud Run** | Yes -- 2M requests/month, scales to zero | ~5-10s for a Python image | Best free option. Docker-native, so the existing Dockerfile works. Needs a card on file for the account, but the free allowance is real. |
| **Render** | Yes | **~30s** after 15 min idle | Simplest to set up. The cold start is the problem: the causal endpoint already takes ~12s warm, so a first request after idle looks broken. |
| **Fly.io** | **No** | ~2s, stays warm | ~$2-5/month for one always-on 512MB machine. Best experience, not free. |

**Recommendation depends on what you are optimising:**

- **Free and acceptable** -- Cloud Run. Scales to zero, so an idle demo costs
  nothing, and a 5-10s cold start is tolerable for a portfolio piece.
- **Free and simplest** -- Render, if you can live with a 30s first load.
- **Best demo experience** -- Fly.io at ~$2-5/month. Worth it if you are sending
  the link to an interviewer and want it instant.

`fly.toml` and the `Dockerfile` are already written; the Dockerfile works
unchanged on Cloud Run and Render, both of which build from it.
