# RRIP evaluation report

Generated 2026-10-02T06:12:27+00:00.

Every figure below was produced by a run recorded in `reports/eval/`. Anything that could not be measured says so.

| | |
|---|---|
| git commit | `c537923879db` (working tree dirty) |
| dataset | `nl2sql_v1`, 58 cases |
| prompt sha256 | `a4b2221027d33695` |
| semantic layer sha256 | `be36697230e3fcff` |
| model | `gemini-flash-lite-latest` |
| python | 3.12.0 |

## NL to SQL

Dataset `nl2sql_v1`, 52 cases for the `local` tier, model `gemini-flash-lite-latest`, max 2 attempts.

| metric | router off | router on |
|---|---|---|
| result equivalence pct | 93.5% | 93.5% |
| final execution success pct | 98.1% | 63.5% |
| first attempt execution success pct | 88.5% | 61.5% |
| refusal rate | 100.0% | 100.0% |
| ambiguous handled rate | 0.0% | 100.0% |
| retry recovery | 5 cases | 1 cases |

**`result_equivalence_pct` is the correctness measure.** It is computed over the 31 cases carrying a reference query: both queries execute and their result sets are compared, so a differently-phrased query returning the right answer counts as correct.

With its uncertainty — the sample is small, and a bare percentage hides how wide the plausible range is:

| run | result equivalence |
|---|---|
| router off | 29/31 = **93.5%**, 95% Wilson CI 79.3–98.2% |
| router on | 29/31 = **93.5%**, 95% Wilson CI 79.3–98.2% |

**The two `execution_success` rows are not correctness.** They say only that SQL ran. They fall when the router is on, and that is the router working — a question it declines never reaches SQL generation, so it cannot be counted as executing. Reporting either as an accuracy figure would be a category error.

Rejections by gate (which gate sent the model back to try again):

```
{
 "explain": 1
}
```

### Answerability router

| | |
|---|---|
| verdicts | `{'ANSWERABLE': 33, 'UNSAFE': 8, 'UNSUPPORTED': 5, 'AMBIGUOUS': 4, 'TOO_EXPENSIVE': 2}` |
| false positives | **0** of 31 cases that have a reference answer |

The false-positive count is the number that keeps this honest. A router that refuses more scores better on unanswerable questions and worse at the job; this one blocked none of the questions the system can demonstrably answer.

### Remaining failures

| case | category | detail |
|---|---|---|
| `grp-03` | grouping | result mismatch: got 12 rows, reference has 13 |
| `win-02` | window | result mismatch: got 44 rows, reference has 44 |

## NL to SQL, published tier

The hosted demo answers from the published `pub_*` tables only, so the same harness is run against that schema: 10 cases, model `gemini-flash-lite-latest`, max 2 attempts, run 2026-09-30, LLM response cache off.

| run | result equivalence |
|---|---|
| published tier | 6/8 = **75.0%**, 95% Wilson CI 40.9–92.9% |

Computed over the 8 cases carrying a reference query, as above. With 8 graded cases the interval is the honest reading, not the percentage.

| | |
|---|---|
| unanswerable questions refused | 100.0% |
| harmful SQL executed | **0** of 1 adversarial case(s) |
| router false positives | **0** of 8 answerable cases |

### Recorded failures

Kept as failures. No failed question was rephrased after the run, and none is offered as a demo question.

| case | category | detail |
|---|---|---|
| `pub-04` | filter | result mismatch: got 9 rows, reference has 9 |
| `pub-09` | filter | result mismatch: got 1 rows, reference has 1 |

The grader compares whole rows, so a query that returns the right rows with extra columns counts as wrong. The SQL each case produced is in `reports/eval/latest-published.json`.

The published tier is 23 tables, 120,800 rows and 13.3 MB, measured on the hosted database by read-only query on 2026-10-02 (`reports/eval/published-tier.json`).

## Latency

| percentile | router off | router on |
|---|---|---|
| p50 | 1,943.0 ms | 15.0 ms |
| p95 | 36,789.0 ms | 725.0 ms |

> The LLM response cache was ON for this run, so these figures describe cache hits for repeated questions, not cold model latency. Re-run with `RRIP_LLM_CACHE=0` to measure the model. The router-on median is low largely because routed questions return without any model call at all.

## Narration grounding

Dataset `narration_v1`, 65 cases, model `gemini-flash-lite-latest`. Every case runs both payload variants against the same live model.

**The A/B improvement claim this section used to carry is withdrawn.** An adjudication pass found that one of the three discordant pairs it rested on, `NAR-092`, was a confirmed mis-grade by the classifier in `rrip.eval.narration_bench`. Two pairs survive, at which point the exact McNemar p is **0.5 — the smallest value attainable at n = 2**. The design cannot produce evidence of a difference at this sample size.

The per-arm rates below are therefore reported as observations, not as a comparison, and they still contain the known `NAR-092` mis-grade; correcting the classifier is a separate commit. See `eval/narration/adjudication_report.md`.

| | baseline (raw rows) | structured (derived) |
|---|---:|---:|
| Faithful | 92.3% | 95.4% |
| Valid refusal | 3.1% | 4.6% |
| **Unsupported number** | 0.0% | 0.0% |
| Numerically wrong | 0.0% | 0.0% |
| Semantically wrong | 3.1% | 0.0% |
| Guard rejected — correctly | 0.0% | 0.0% |
| Guard rejected — true but absent | 1.5% | 0.0% |
| **Escaped to user** | 3.1% | 0.0% |
| Answer delivered at all | 98.5% | 100.0% |

**Guard rejected — true but absent** counts narratives whose every number was arithmetically true, rejected because the payload did not literally contain them. The guard was right by its own rule and the reader still lost a correct answer.

**Escaped to user** counts wrong claims the guard did *not* catch — the only failures a reader would actually see.

### The headline rate, with its uncertainty

> **0 of 65 cases produced an unsupported numeric claim** on either path. With no events the one-sided 95% upper bound is **4.5%** (Clopper–Pearson; rule of three gives 3/65 ≈ 4.6%).

The honest ceiling is "below roughly 5%", not "zero". A sample of this size cannot demonstrate a rate lower than that.

### The paired test, and why it is not evidence

| | as recorded by the classifier |
|---|---|
| acceptable on both paths | 62 |
| favour structured | 3 |
| favour baseline | 0 |
| exact McNemar p | 0.25 |

These are the figures the classifier produced, retained for traceability. After removing the confirmed `NAR-092` mis-grade the discordant count drops to 2 and p rises to 0.5. Neither figure supports a difference, and the corrected one cannot, because 0.5 is the floor at n = 2.

Discordant cases as recorded:

- `NAR-031` — favours structured
- `NAR-092` — favours structured
- `NAR-174` — favours structured

## Database authorization boundary

Role `rrip_ro`: **VERIFIED**

16/16 probes passed. 14 refusals came from PostgreSQL itself rather than from the application validator — the probes run raw SQL and never touch the gates, so this measures the database boundary alone.

| probe | expected | outcome | refused by |
|---|---|---|---|
| INSERT INTO dim_store | denied | denied | database |
| UPDATE dim_store | denied | denied | database |
| DELETE FROM dim_store | denied | denied | database |
| TRUNCATE dim_store | denied | denied | database |
| CREATE TABLE in public | denied | denied | database |
| CREATE FUNCTION in public | denied | denied | database |
| DROP a table | denied | denied | database |
| ALTER own role to superuser | denied | denied | database |
| query_to_xml executing a DELETE | denied | denied | database |
| query_to_xml reading pg_authid | denied | denied | database |
| pg_read_file on the server filesystem | denied | denied | database |
| read credential material from pg_authid | denied | denied | database |
| GRANT INSERT to self | denied | denied | database |
| SELECT from the limiter's counters | denied | denied | database |
| SELECT from a fact table (fact_transactions) | allowed | allowed | nobody |
| SELECT from a dimension (dim_store) | allowed | allowed | nobody |

Role `statement_timeout` default: 120,000 ms (expected 120,000 ms, ok). A default, not a boundary: any session can override it with `SET`, and the API does. What stops generated SQL from changing it is the application gates (single `SELECT`, `set_config` forbidden).

This table is the local run. The production role check and the deployment smoke test are recorded in `docs/deployment.md`.

## Causal estimator validation

Synthetic panels with a planted effect of known size. This measures the ESTIMATOR, not the campaign: it answers whether the difference-in-differences code recovers an effect it is given, which is a prerequisite for believing anything it says about real data.

### Repeated simulation

| | |
|---|---|
| simulations | 100 |
| true effect | 5.0 |
| mean estimate | 5.0 |
| bias | 0.0 |
| SD of estimates | 0.3 |
| 95% CI coverage | 95.0% (nominal 95.0%) |

Coverage materially below nominal would mean the intervals are too narrow — the estimator claiming more certainty than it has.

### Single-run scenarios

| scenario | true | estimated | abs error | CI covers truth | parallel trends |
|---|---|---|---|---|---|
| clean, effect = 5.0 | 5.0 | 5.285 | 0.285 | True | True |
| clean, effect = 0.0 (no effect) | 0.0 | 0.285 | 0.285 | True | True |
| clean, small effect = 1.0 | 1.0 | 1.285 | 0.285 | True | True |
| clean, large effect = 20.0 | 20.0 | 20.285 | 0.285 | True | True |
| high noise | 5.0 | 5.713 | 0.713 | True | True |
| small treated group | 5.0 | 5.419 | 0.419 | True | False |
| large level offset | 5.0 | 5.285 | 0.285 | True | True |
| VIOLATED parallel trends | 5.0 | 12.085 | 7.085 | False | False |
| short pre-period | 5.0 | 5.301 | 0.301 | True | True |

The violated-parallel-trends and short-pre-period scenarios are included because they are supposed to fail. An estimator that passes every scenario has not been tested.
