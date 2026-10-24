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
39.6M-row load                            120,800 rows in 23 published tables
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

23 tables, 120,800 rows and 13.3 MB on the hosted database:
17 computed tables and 6 dimensions, comfortably inside every free tier.
Measured by read-only query on 2026-10-02 and recorded in
[`reports/eval/published-tier.json`](../reports/eval/published-tier.json);
`tests/test_docs_consistency.py` fails if this page, the README and the
evaluation report stop agreeing on it.

| Table | Rows | What it holds |
|---|---:|---|
| `pub_weekly_revenue` | 102 | Weekly revenue, cumulative, rolling average |
| `pub_weekly_revenue_by_dept` | 2,442 | The same, per department, for drill-down |
| `pub_overview_totals` | 45 | Panel totals, overall and per department |
| `pub_anomalies` | 9 | Weeks flagged by the z-score rule |
| `pub_headline` | 5 | Headline figures the publisher fills from measured values |
| `pub_rfm_segments` | 7 | RFM segment aggregates |
| `pub_retention_tenure` | 72 | Relative-tenure retention curves |
| `pub_pareto_products` | 5,000 | Top products with cumulative revenue share |
| `pub_commodity_affinity` | 11,292 | Market basket lift pairs |
| `pub_reorder_by_department` | 12 | Reorder rates |
| `pub_promo_exposure` | 1,901 | Promotional display rate by department and week |
| `pub_causal_results` | 3 | Pre-computed DiD for campaigns 26, 8, 18 |
| `pub_causal_pretrend` | 158 | Pre-period series for the parallel-trends plot |
| `pub_forecast` | 1,725 | Stored forecast and interval per department and week |
| `pub_forecast_history` | 1,725 | Actuals beside forecasts, for the chart |
| `pub_forecast_departments` | 23 | Modelled departments with their test error |
| `pub_forecast_summary` | 1 | The model card |
| `pub_dim_product` | 92,353 | Product dimension, so NL→SQL has a real schema |
| `pub_dim_household` | 2,500 | Household dimension: one row per panel household, no purchases |
| `pub_dim_date` | 711 | Calendar days |
| `pub_dim_store` | 582 | Stores |
| `pub_dim_week` | 102 | Panel weeks |
| `pub_dim_campaign` | 30 | Campaigns |

### What is deliberately NOT published

- **`fact_causal`** — 3.2 GB, and nothing in the dashboard reads it row-by-row.
- **`fact_transactions`** — 483 MB. Excluding it means hosted NL→SQL cannot
  answer arbitrary transaction-level questions. **That limitation must be stated
  in the UI**, not discovered by a user whose question returns nothing.
- **Raw household-level rows** — the panel is licensed for research use;
  publishing per-household transaction data to a public endpoint is a licence
  question, not just a size one. The household *dimension* is published (one
  row per household, no purchases), so the hosted tier is not free of
  household-level data; it is free of household-level transactions.

## Honest consequences

1. **NL→SQL is narrower when hosted.** The gates and retry behaviour are
   identical — that is the feature — but the schema it writes against is
   aggregate tables, so "which households bought X" is unanswerable. The hosted
   UI should say which tables are available. Measured on this schema the
   benchmark scores 6/8 result-equivalent (95% Wilson interval 40.9–92.9%),
   against 29/31 = 93.5% (95% Wilson interval 79.3–98.2%) on the local tier;
   both runs are in [`reports/eval/latest.md`](../reports/eval/latest.md).
2. **Causal results are precomputed, not live.** The estimation runs locally and
   the result is published. A hosted user sees the estimate, the confidence
   verdict and the pre-trend plot, but cannot re-run it against a different
   campaign or window.
3. **Phase 2 numbers cannot be reproduced on the hosted tier.** They are
   measurements of a specific machine with 36.8M rows. `docs/performance.md`
   already states its hardware; the hosted deployment does not invalidate it,
   but nor can it demonstrate it.

## Publishing

`rrip publish` (see [`src/rrip/publish/publisher.py`](../src/rrip/publish/publisher.py)):

1. Connects to local and to `RRIP_PUBLISH_DSN`.
2. Drops and recreates each `pub_*` table inside its own transaction, so a
   failure cannot leave a half-written table visible to readers.
3. Recomputes each aggregate from the local star schema (and the causal results
   from statsmodels, and the stored forecasts).
4. `COPY`s each table out of local and into the target in binary format.
5. Records a `pub_manifest` row per table with row counts and a timestamp, so a
   stale deployment is detectable rather than assumed fresh.

Publishing is idempotent and refresh is manual: the panel ended at day 711, so
the data does not change.

**One limitation to know.** `push()` rebuilds each hosted table's DDL from the
local `information_schema` column types, which carries the columns but **not the
primary keys or indexes**. At 13.3 MB and 120,800 rows that costs nothing in
practice, and every hosted query is a small scan, but uniqueness is not enforced
on the hosted copy — it is trusted to match what local built. Re-deriving the
keys on the target is a worthwhile follow-up, not a correctness bug today.

## What I need from you

| # | What | Where | Why | Notes |
|---|---|---|---|---|
| 1 | **Neon account + connection string** | [neon.tech](https://neon.tech) | Hosted Postgres for the `pub_*` tables | Free tier. Create the project with **C collation** if the option is offered, so ordering matches local. Put the DSN in `.env` as `RRIP_PUBLISH_DSN`. |
| 2 | **Vercel account** | [vercel.com](https://vercel.com) | Next.js frontend hosting | Free Hobby tier. Connect it to the GitHub repo; set root directory to `frontend/`. |
| 3 | ~~A host for the FastAPI service~~ | -- | **Superseded:** the API runs as a Vercel Python function (`frontend/api/index.py`) on the same origin as the frontend. | The function installs `rrip` at an **exact commit** with pinned runtime dependencies (`frontend/api/requirements.txt`), never `@main`. |
| 4 | **Gemini API key for the hosted env** | already have | NL→SQL and narration | Set as an environment variable on the API host — **not** committed. The rotated key is fine. |
| 5 | **GitHub repository** | [github.com](https://github.com) | CI runs on every pull request; Vercel deploys from `main` | Public repo, so the production check reads CI and `/health` unauthenticated. |
| 6 | **Read-only role on the published database** | Neon SQL editor or `psql` | The NL→SQL endpoint is public; the validator is defence in depth, the role is the boundary | Run [`sql/ddl/60_readonly_role.sql`](../sql/ddl/60_readonly_role.sql) **after** `rrip publish` creates the tables, then set `RRIP_PG_READONLY_DSN` to a DSN for `rrip_ro`, and do **not** give the API `RRIP_PG_DSN` at all. See below. |
| 7 | **Limiter role on the published database** | Neon SQL editor or `psql` | The AI endpoints spend Gemini calls; without the counters they refuse on the published tier | Run [`sql/ddl/70_api_limits.sql`](../sql/ddl/70_api_limits.sql) **after** step 6, then set `RRIP_LIMITER_DSN` and `RRIP_TRUSTED_IP_HEADER=x-vercel-forwarded-for` in the Vercel environment. See below. |

**Not needed:** any domain, any Anthropic key.

### The read-only role is not optional

Anyone on the internet can type a question into `/query` and cause SQL to be
planned and executed. The six validation gates in `src/rrip/ai/nl2sql.py` reject
what they can recognise, but they are string analysis, and one bypass —
`SELECT query_to_xml('DELETE FROM …', …)`, which executes its own text argument
— passed every gate that existed before the function allowlist was added.

So the API must not connect as the table owner:

```bash
psql "$RRIP_PUBLISH_DSN" -v ro_password='<generate one>' -f sql/ddl/60_readonly_role.sql
```

Then set `RRIP_PG_READONLY_DSN` to the same connection string with the user and
password swapped for `rrip_ro`. That one name is what the API pool connects with
**and** what `rrip verify-role` checks, so a passing verification is a statement
about the serving connection. `RRIP_PG_DSN` is the loader's owning credential and
must not be set on the deployment at all; on the published tier the API refuses
to start its pool without `RRIP_PG_READONLY_DSN` rather than fall back to it.
(An earlier version of this line said to put `rrip_ro` in `RRIP_PG_DSN` -- the
wrong variable -- and the first deployment ran its public NL->SQL endpoint as the
database owner.) Run it again after any `rrip publish` that adds a table —
`ALTER DEFAULT PRIVILEGES` covers new tables created by the same owning role,
but not tables created by a different one.

Verify from the API's own credentials, not the owner's:

```bash
psql "$RRIP_PG_READONLY_DSN" -c "CREATE TABLE probe (x int)"   # must fail: permission denied
psql "$RRIP_PG_READONLY_DSN" -c "DELETE FROM pub_headline"     # must fail: permission denied
psql "$RRIP_PG_READONLY_DSN" -c "SELECT count(*) FROM pub_headline"   # must succeed
```

Two failures and one success is the passing result. If the first two succeed,
the API is still connecting as the owner and the boundary does not exist.

### The limiter role is not optional either

The AI endpoints spend model calls on the project's Gemini key, and anyone can
reach them. `src/rrip/api/limits.py` bounds that with a per-IP rate limit and a
global daily cap, both counted in Postgres because serverless instances share
no memory. The counters need writes, which `rrip_ro` must never have, so they
get their own role:

```bash
psql "$RRIP_PUBLISH_DSN" -v limiter_password='<generate one>' -f sql/ddl/70_api_limits.sql
```

Then, in the Vercel project environment (never committed):

| Variable | Value |
|---|---|
| `RRIP_LIMITER_DSN` | the Neon DSN with the user and password swapped for `rrip_limiter` |
| `RRIP_TRUSTED_IP_HEADER` | `x-vercel-forwarded-for` |
| `RRIP_CORS_ORIGINS` | the deployed site's origin, e.g. `https://<project>.vercel.app` -- the same list gates the AI endpoints' origin check |
| `RRIP_AI_RATE_LIMIT`, `RRIP_AI_RATE_WINDOW_SECONDS`, `RRIP_LLM_DAILY_CAP` | optional; defaults 10 per 60 s and 300 calls per UTC day |

On the published tier the AI endpoints **refuse** until `RRIP_LIMITER_DSN` is
set, and refuse while the counter store is unreachable. That is deliberate: a
limiter that switches itself off when its database is down is bypassed by an
outage. `GET /api/v1/ai/status` reports `"protection": "on"` once it is wired,
without spending a model call -- check it after every deploy.

`x-vercel-forwarded-for` is trusted because Vercel sets it at its edge: its
request-header documentation states that it overwrites `x-forwarded-for` and
does not forward external IPs, to prevent spoofing, and that
`x-vercel-forwarded-for` carries the same value but cannot be overwritten even by
a proxy placed in front of Vercel. That was then verified on the deployment, not
assumed -- see below.

### Monitoring (optional, off until configured)

Both integrations are in the code and do nothing until switched on. Neither
has been verified against live data yet.

**Sentry (API errors).** `src/rrip/api/observability.py` initialises the SDK
only when `SENTRY_DSN` is set, with request bodies, local variables, PII and
outbound trace headers off, and every event, transaction and breadcrumb run
through a scrubber first. `tests/test_observability.py` checks that scrubber
with synthetic secrets.

1. Create a Sentry project (platform: Python / FastAPI) and copy its DSN.
2. In the Vercel project environment (never committed): `SENTRY_DSN`, and
   optionally `SENTRY_ENVIRONMENT` (defaults to `RRIP_TIER`) and
   `RRIP_SENTRY_TRACES_SAMPLE_RATE` (defaults to `0.0`, which means errors only).
3. Before relying on it, send one harmless event from a local shell with the
   DSN in `.env` and `SENTRY_ENVIRONMENT=dev`, then open it in Sentry and check
   that it carries no headers, body or query string you would not publish:

   ```bash
   python -c "from rrip.api.observability import init_sentry; import sentry_sdk; assert init_sentry(); sentry_sdk.capture_message('rrip monitoring check'); sentry_sdk.flush()"
   ```

The API deliberately has no endpoint that raises a test error, because a
public route that produces errors on demand is an abuse vector.

**Merging does not activate the API side.** The Vercel function does not run
the repository's `src/`; it runs `frontend/api/index.py`, which imports
`rrip.api.main` from the package that `frontend/api/requirements.txt` installs,
and that is pinned to one commit (`rrip @ git+…@<sha>`). Until the pin moves,
production runs the pinned commit's code, with `sentry-sdk` installed and never
imported. The Next.js frontend, Speed Insights included, is built from the
merged repository and changes on the next deploy.

A commit cannot pin itself, since its SHA depends on its contents, so the pin
always moves in a separate, later commit:

1. Merge the source change into `main`, after anything it depends on (it is
   built on `release-hardening`).
2. Take the commit as it exists on `origin/main`, meaning the merge or squash
   commit. Do not use a feature-branch SHA: a squash merge leaves branch
   commits off `main`, and once the branch is deleted GitHub need not keep them.
   Check it with `git merge-base --is-ancestor <sha> origin/main`, and confirm
   `git cat-file -e <sha>:src/rrip/api/observability.py` succeeds.
3. In a new commit, set the pin to that full 40-character SHA (the
   `test_function_requirements.py` tests require the full SHA). Merge it once CI
   is green.
4. In that deployment's Vercel build log, check that pip installed `rrip` at
   the new SHA. The API does not report its own version, so the build log is
   the evidence.
5. Only then set `SENTRY_DSN` and run the check event above.

**Vercel Speed Insights (real-user Core Web Vitals).** `<SpeedInsights />` is
mounted in `frontend/app/layout.tsx`. Enable Speed Insights in the Vercel
dashboard (Project -> Speed Insights), then deploy. Data appears only after
real visits to the deployed site. The script and beacon are served same-origin
under `/_vercel/speed-insights/`, so the CSP in `next.config.js` does not block
them.

## Verified deployment (2026-09-30)

```
Browser
  │
Vercel edge ── sets x-vercel-forwarded-for; client-supplied forwarding headers discarded
  │
FastAPI (Vercel Python function, rrip pinned to an exact commit)
  ├── origin check          RRIP_CORS_ORIGINS; friction, not identity
  ├── per-IP rate limit ──► Postgres, as rrip_limiter (two counter tables only)
  ├── daily LLM cap     ──► Postgres, as rrip_limiter; charged per paid model attempt
  ├── NL->SQL gates         single SELECT, keyword/function allowlist, EXPLAIN cost, row cap
  └── analysis          ──► Postgres, as rrip_ro (SELECT on pub_* only)
                        ──► Gemini (model fallback chain); failure -> explicit 503 AI_UNAVAILABLE
```

`https://retail-revenue-intelligence-flame.vercel.app`, published tier, 23
`pub_*` tables republished 2026-09-29. Production environment:
`RRIP_TIER`, `RRIP_PG_READONLY_DSN`, `RRIP_LIMITER_DSN`, `GEMINI_API_KEY`,
`RRIP_CORS_ORIGINS`, `RRIP_TRUSTED_IP_HEADER=x-vercel-forwarded-for`,
`RRIP_LLM_DAILY_CAP=300`. No owner credential (`RRIP_PG_DSN`) is present.

**Before this pass**, production ran code without any AI endpoint protection
(the function tracked `@main`), with a live Gemini key, and its NL->SQL
endpoint connected as the Neon owner (no `rrip_*` role existed). The key was
pulled first; the hardened build replaced it.

### Database roles, verified in production

`rrip verify-role` through the production `RRIP_PG_READONLY_DSN`: **VERIFIED,
16/16**, connected as `rrip_ro`, tier `published`. Refused by Postgres itself:
INSERT/UPDATE/DELETE/TRUNCATE/DROP on `pub_dim_store`, CREATE TABLE/FUNCTION,
ALTER ROLE SUPERUSER, GRANT to self (no effect), `query_to_xml` write and
`pg_authid` read, `pg_read_file`, `pg_authid` directly, and reading the limiter
counters. Allowed: SELECT on `pub_weekly_revenue` and `pub_dim_store`. Role
`statement_timeout` default 120 s (a default, not a boundary). `rrip_limiter`
can write the two counter tables and nothing else; `rrip_ro` cannot read them.

### Public API security matrix (from source)

| Endpoint | Calls LLM? | Rate limited? | Daily cap? | Origin checked? | DB touched? |
|---|---|---|---|---|---|
| `POST /api/v1/ai/query` | yes, up to 3 attempts | yes | yes, per attempt | yes | `rrip_ro` (EXPLAIN + execute), `rrip_limiter` |
| `POST /api/v1/ai/ask` | only if the matcher finds no department | yes | yes, when called | yes | `rrip_ro` (`pub_forecast*`), `rrip_limiter` |
| `POST /api/v1/ai/narrate` | yes | yes | yes | yes | `rrip_limiter` only |
| `GET /api/v1/causal/analysis/{id}?propose=true` | local tier only; published returns precomputed results first | yes (local) | yes (local) | yes (local) | `rrip_ro` |
| `GET /api/v1/ai/status` | no | no | no | no | no |

`tests/test_api_limits.py` walks the app's routes and fails if any route that
obtains a provider is unprotected.

### Smoke test against production (`rrip smoke`, 2026-09-30)

All 13 checks passed on the final run; the two earlier runs found one real bug
and one test bug, both fixed.

| Test | Result |
|---|---|
| A liveness / readiness / protection / analytics | 200, 23 tables, `protection: on`, overview from `pub_*` |
| B NL->SQL benchmark prompt pub-02 | 200, 5 rows, ~4.0-4.6 s end to end |
| C adversarial prompt | refused by the router, no model call |
| D per-IP limit | request 11 in one window -> 429 |
| E daily cap (test cap 6) | 429 `DAILY_LLM_CAP` once reached |
| F foreign origin | 403 `ORIGIN_NOT_ALLOWED` |
| G analytics while AI is capped | 200 |
| H warm / concurrent | 20 sequential p50 ~300 ms, p95 ~360-570 ms; 15 concurrent all 200, p50 533 ms warm (~3 s while the platform scaled from cold) |
| I spoofed client IP | every request carried a new spoofed IP in all three forwarding headers; still 429 at 11 |

**Client IP, verified in the database:** 33 requests carrying 33 different
spoofed addresses across three runs produced **one** bucket in `api_rate_limit`
-- spoofing buys nothing, and the proxy-derived address is what is counted.
**Not verified in production:** that two different real clients get separate
buckets; there was only one client host, with no IPv6 route. It is covered
against real Postgres by `tests/test_api_limits_db.py`.

**Daily cap semantics, observed:** eight identical `/ask` questions charged the
counter only on cache misses -- cache hits are free by design. The counter reads
one past the cap after a refusal; the refused call never reached the provider.

**Analytics pool on serverless:** cold, 20 warm sequential, 15 concurrent, three
runs -- no failures, no 5xx, and no error or warning in the production runtime
logs. Left unchanged.

**Found and fixed by the smoke test:** `/ask` read the on-disk forecast
artifact, which the function does not have, so every forecast question returned
503 in production; it now reads `pub_forecast*` on the published tier.

After testing, the day's counter was reset and the cap raised to 300.

### Why no separate API host

The API runs as a Vercel Python function (`frontend/api/index.py`) on the same
origin as the frontend, so there is no second service to host, no CORS hop
between them, and one deploy ships both. That is only possible because the
published tier does not import the scientific stack: the core dependencies are
~30 MB against Vercel's 250 MB function limit, while the `pipeline` extra
(pandas, statsmodels, scipy) is 332.9 MB and stays local (see `pyproject.toml`).

An earlier revision shipped a `Dockerfile` and a `fly.toml` for running the API
as a standalone container on Fly.io or Cloud Run. They were removed once the
Vercel-function deployment was verified: keeping deployment manifests for a path
the project does not use is a maintenance liability and a source of drift. If a
container host is ever needed again, the API is a plain ASGI app
(`uvicorn rrip.api.main:app`) and a Dockerfile is a few lines.
