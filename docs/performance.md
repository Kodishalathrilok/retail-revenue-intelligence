# Performance

Every number here comes from a real run on the machine described below. Nothing
is estimated, extrapolated, or carried over from a previous configuration.

## Platform, and what it limits

| | |
|---|---|
| CPU | Intel i5-1235U, 10 cores / 12 logical |
| RAM | **7.7 GB** — the binding constraint |
| Storage | KIOXIA NVMe SSD |
| OS | Windows 11 |
| Postgres | 16.13, UTF8 encoding, **C collation** |
| Power | **AC only.** `STANDBYIDLE` and `HIBERNATEIDLE` are 0 on AC; on battery the machine still sleeps after 180s |

**Any timed run on this machine must be on AC power.** That is not housekeeping
— it is part of the hardware specification, because a run on battery sleeps
after 180 seconds of no interactive input and the sleep time lands inside the
measurement. The first Phase 1 load is a worked example of exactly that failure;
see the next section.

**Platform limits, stated up front rather than buried.** A benchmark that
doesn't disclose what its platform cannot do isn't a benchmark.

- **`effective_io_concurrency` is forced to 0.** This setting drives
  `posix_fadvise()` prefetching, which Windows does not implement — Postgres
  rejects any other value outright. The usual SSD recommendation of 100–200 is
  Linux-only. **Consequence: bitmap heap scans get no prefetch here, so those
  plans are slower on this machine than the identical query would be on Linux.**
  Any plan in this document involving a bitmap heap scan carries that penalty.
- **7.7 GB RAM against a 3.2 GB fact table** means the working set does not fit
  in cache. Timings are I/O-sensitive, so queries are reported cold and warm
  rather than as a single number.
- **C collation** was chosen over the installed `English_India.1252` for
  byte-order comparisons, faster sorts, and identical behaviour on Linux CI.
  Text here is uppercase ASCII product data, so locale-aware collation bought
  nothing and cost cross-platform determinism.

Configuration is version-controlled in `sql/ddl/00_tuning.sql` and applied via
`ALTER SYSTEM`. Capture the live values with `sql/ddl/99_verify_settings.sql`.

| Setting | Baseline | During bulk load |
|---|---|---|
| `shared_buffers` | 1GB | 1GB |
| `work_mem` | 32MB | 32MB |
| `maintenance_work_mem` | 512MB | 1GB |
| `max_wal_size` | 4GB | 8GB |
| `synchronous_commit` | on | off |
| `random_page_cost` | 1.1 | 1.1 |
| `effective_io_concurrency` | 0 (forced) | 0 (forced) |

## Measurement integrity: the first load was 72% sleep

**The first measured Phase 1 load took 75.2 minutes. 72% of that — 3,247 of
4,513 seconds — was the machine sitting in Modern Standby, not doing work.**

After the fix described in Entry 1, the same load took 15.8 minutes. Comparing
those two numbers directly gives **4.8×**, and that figure would have been
wrong. The optimization is worth **1.40×**. The rest was a laptop asleep.

This section exists because that mistake was one sentence away from being
published, and because the same trap is open for every timing in this document.

### The evidence

The original run's 93 batches had a median of 14.1s. Windows Kernel-Power 506
(enter standby) and 507 (exit standby) events map onto the four slowest almost
exactly:

| Batch | Started | Duration | Standby window | Standby duration |
|---|---|---:|---|---:|
| w32 | 12:30:26 | 22m 02s | 12:30:18 → 12:52:22 | 22m 04s |
| w99 | 13:22:16 | 17m 47s | 13:21:10 → 13:40:04 | 18m 54s |
| w59 | 12:59:14 | 12m 50s | 12:59:09 → 13:11:51 | 12m 42s |
| w58 | 12:57:46 | 1m 28s | 12:57:39 → 12:59:09 | 1m 30s |

The machine idled into standby whenever no interactive command was running,
suspending the load mid-batch. The optimized run stayed awake: maximum batch
12.1s, nothing above it.

**w58 was nearly missed.** The first pass at this analysis filtered on "batches
over 100 seconds", which caught three. w58 took 88.1s — six times the median,
plainly anomalous, and below an arbitrary threshold chosen before anyone knew
what the distribution looked like. It only surfaced when the *maximum excluding
the known three* turned out to be 88.1s rather than something near the median.

Excluding all four, the remaining 89 batches have a mean of 14.23s and a maximum
of 31.4s — a distribution with no outliers left in it. That is the number the
"before" column is built from.

### What was ruled out first

The stall pattern had several plausible database explanations. Each was measured
before power management was considered:

- **Data volume** — ruled out. The four affected batches averaged 411,037 rows
  against 394,687 for the other 89, a 4% difference. The largest batch in the
  run — 589,473 rows — was not among them and completed normally.
- **Checkpoint pressure** — ruled out. `checkpoints_req` was 27 against
  `checkpoints_timed` 2,597, roughly 1%. `max_wal_size` at 8GB was not the
  constraint. (`pg_stat_bgwriter` is cumulative since 2026-07-07 and was not
  snapshotted around the load window, so this is a ratio argument rather than an
  isolated measurement; the lopsidedness makes it conclusive anyway.)
- **Antivirus and cloud sync** — ruled out. OneDrive was not running, and
  Defender's most recent scan was two days before the load window.
- **Memory pressure** — plausible on 7.7 GB but not causal: the stalls
  correlate with standby transitions to the second, not with memory conditions.

### Why the correlation was detectable at all

By accident. `etl_batch_log.logged_at` defaulted to `now()`, which in Postgres
returns *transaction start* time — and the batch INSERT and its log row share a
transaction. So `logged_at` recorded when each batch **began**, not when it was
logged. That is what allowed each slow batch to be lined up against a standby
window.

Had it recorded the intended insert time, every stalled batch would have been
timestamped at the *end* of its stall, and the correlation with the standby
entry events would have been far less obvious.

The column now defaults to `clock_timestamp()`, which is what was meant. The
accident is recorded here because the diagnosis depended on it.

### The rule this establishes

**Never compare against the uncorrected 75.2-minute figure.** It is not a
baseline; it is a baseline plus 54 minutes of sleep. Where a before/after is
reported in this document, the "before" is the standby-corrected **22.1
minutes** — the original run's 89 unaffected batches at their measured mean of
14.23s, extrapolated across all 93.

Two practices specific to this machine:

1. **Confirm AC power and a zero standby timeout before any timed run.** On a
   laptop, an unattended benchmark measures the power policy as much as the
   query.
2. **Report the distribution, not the total.** A mean hides a 90× outlier
   completely, and the total merely looks disappointing rather than obviously
   wrong. Median, max and outlier count would have exposed this immediately.

And one that generalises well past this project:

### Never filter outliers on a threshold chosen before seeing the distribution

The first pass at this analysis asked for *batches over 100 seconds*. It
returned three. The number 100 was not derived from anything — it was picked
because the known stalls were in the four-figure range and 100 felt safely
below them.

`w58` took 88.1 seconds. Six times the median. Unambiguously anomalous. Invisible
to the filter.

It was found by asking a different question with no threshold in it: **what is
the maximum, once the outliers I already know about are removed?** The answer
came back 88.1s instead of something near the median, which is itself the
finding — a clean distribution would have returned roughly 30s.

The general form:

> A threshold encodes what you already believe the distribution looks like. When
> the distribution is the thing under investigation, the threshold is a
> hypothesis being smuggled in as a filter — and anything it excludes is
> invisible rather than merely absent.

The safe alternatives ask the data to describe itself: sort and look at the tail;
take the max after removing known cases; compare against the median rather than
a constant; plot it. All of these would have caught `w58`. The threshold was the
only approach that could not.

This is a general analysis practice, not a benchmarking one. It applies directly
to the Phase 4 data quality assertions — where a null-rate or row-count-drift
threshold picked before profiling would fail in exactly this way — and to the
Phase 6b anomaly detection, where a z-score cutoff chosen in advance decides
which anomalies are capable of being reported at all.

## Cache state: cold and warm are nearly the same thing here

Benchmark write-ups usually treat cold and warm as a given distinction worth
reporting separately. One measurement here was taken with `shared_buffers`
genuinely cleared by an elevated service restart, to calibrate the warm-only
numbers against. It showed the distinction is close to meaningless for this
workload — and the buffer counts explain why.

| Cache state | Execution | `shared_hit` | `shared_read` | I/O read time |
|---|---:|---:|---:|---:|
| **cold `shared_buffers`** | 4,493.0 ms | 151 | 199,904 | 107.1 ms |
| warm | 5,281.9 ms | 152 | 199,903 | 30.7 ms |
| warm | 4,571.9 ms | 149 | 199,903 | 1.4 ms |
| warm | 4,230.0 ms | 149 | 199,903 | 0.5 ms |

**Cold was not slower than warm.** Three things follow, and they matter more
than the contrast the pair was taken to provide.

### `shared_read` does not mean disk I/O

It means "not found in `shared_buffers`". Q1 reads 199,903 blocks — 1,562 MB — on *every* execution
regardless of cache state, because Postgres uses a small ring buffer
(`BAS_BULKREAD`) for large sequential scans specifically so they cannot evict
the entire cache. A large scan therefore never populates `shared_buffers` with
its own pages, and repeat executions look identical to a first one.

### The pages come from the OS file cache, which the restart did not clear

And which cannot be cleared on Windows at all. Total I/O wait on the cold run was
107 ms out of 4,493 ms: **2.4% of runtime**. Q1 is CPU-bound on the scan and
join, not I/O-bound.

### `pg_prewarm`'s effect survives exactly one execution

The first warm repetition of the before-run recorded `shared_hit=38,977` — the
prewarmed pages. Every subsequent repetition recorded `shared_hit≈149`, because
the ring buffer flushed them. That is direct evidence for the prewarm limitation
disclosed above, rather than an assumption about it.

**Run-to-run variance exceeds the cold/warm difference.** Q1's warm executions
span 4,230–7,738 ms — a factor of **1.83×** — against a cold/warm I/O difference
of ~107 ms. Reporting a single cold number against a single warm number would
have described noise. This is why medians over 5 repetitions are reported with
min and max, and why the labels matter less than the buffer counts.

For this workload, "cold" and "warm" are close to meaningless distinctions. That
is a finding, not a limitation of the method.

## Entry 1 — Phase 1 load: raw ingest vs constrained insert

### The headline: same rows, same machine, 14× apart

The clearest result in this entry is not the optimization. It is the gap between
moving bytes and moving bytes *through constraints*, measured on identical data:

| Operation | Rows | Time | Throughput |
|---|---:|---:|---:|
| `COPY` into unlogged staging | 36,786,524 | **69.1s** | **532,367 rows/s** |
| `INSERT … SELECT` into partitioned fact | 36,771,279 | 945.5s | 37,827 rows/s (median) |

**14.1× slower**, same file, same session. The difference is partition routing,
primary-key maintenance, WAL, and a `DISTINCT ON` sort — not I/O. Sequential
read of the 664 MB source file measures 1.1 GB/s, so the disk was never the
constraint at any point in this load.

This is why the loader stages first and inserts second, rather than parsing rows
in Python: the expensive part was never the reading.

### The optimization

**Original ordering.** `fact_causal` declared its `PRIMARY KEY` inline in the
table DDL, and each per-week batch used `ON CONFLICT (week_no, product_id,
store_id) DO NOTHING`.

Two costs, both avoidable:

1. **The PK was maintained during insert.** This project already deferred
   secondary indexes to a post-load step — but that does nothing for a primary
   key, because the PK is part of the table definition. All 93 partitions
   maintained a unique index across all 36.8M inserted rows.
2. **`ON CONFLICT` forced an index probe per row** — 36.8M probes to guarantee
   something already guaranteed. Uniqueness comes from `DISTINCT ON` collapsing
   duplicates within a batch, plus each batch selecting exactly one `week_no`,
   which is the leading key column, so batches cannot collide with each other.

**Corrected ordering.** `fact_causal` is created without a PK; the PK is added
in `40_indexes.sql` after load, alongside the secondary indexes. The `ON
CONFLICT` clause is removed.

### Results

Both runs moved the same 36,771,279 rows. The "before" column is
**standby-corrected** throughout, for the reasons given above.

| | Original (standby-corrected) | Optimized |
|---|---:|---:|
| Median batch | 14.0s | **10.1s** |
| Mean batch | 14.23s | **10.16s** |
| Median throughput | 27,518 rows/s | **37,827 rows/s** |
| Max batch | 31.4s | **12.1s** |
| **Total** | **~22.1 min** | **15.8 min** |
| **Speedup** | | **1.40×** |

Post-load steps, optimized run: foreign key validation 58.2s, index and deferred
PK build 189.8s.

The optimization improves steady-state cost, which was never what dominated the
original run's wall clock. Both facts are true and neither substitutes for the
other: the ordering was genuinely wrong and is now fixed, *and* the headline
75-minute figure was mostly a sleeping laptop.

### Other load measurements

| Step | Rows | Time | Throughput |
|---|---:|---:|---:|
| `COPY` transaction_data → staging | 2,595,732 | 8.33s | 311,613 rows/s |
| `COPY` causal_data → staging | 36,786,524 | 69.10s | 532,367 rows/s |
| `fact_transactions` insert (24 monthly batches) | 2,595,732 | 52.82s | 49,143 rows/s |
| `fact_causal` insert (93 weekly batches) | 36,771,279 | 945.52s | 38,890 rows/s |
| Foreign key validation | — | 58.2s | — |
| Index + PK build | — | 189.8s | — |

`fact_transactions` keeps its PK inline deliberately: at 2.6M rows the total
insert cost is 52.8s, so deferring it would save seconds while giving up
key enforcement from the first row.

### Resulting sizes

| Object | Size | Partitions |
|---|---:|---:|
| `fact_causal` | 3,193 MB | 102 |
| `fact_transactions` | 483 MB | 24 |
| Staging tables (droppable) | 1,782 MB | — |
| Database total | 5,494 MB | — |

## Entry 2 — Phase 2 query optimization

Five analytical queries, each expensive for a **different** mechanism, measured
before and after a distinct intervention per query.

### Methodology, including what was set up beforehand

Every measurement is recorded in `perf_measurement` and this section is written
from those rows. Nothing here is transcribed by hand.

**Repetitions and statistics.** 5 warm repetitions per query. Median, min and max
are reported — never a bare mean. Measurements are taken via
`EXPLAIN (ANALYZE, BUFFERS, TIMING, FORMAT JSON)` with `track_io_timing` on.

**Cache state is warm, and labelled warm.** Clearing `shared_buffers` requires
restarting the Postgres service, which requires elevation the benchmark process
does not have. Rather than label a warm run "cold", every measurement records
`shared_hit` against `shared_read` so the actual cache state is visible as data
rather than asserted. Windows has no supported way to clear the OS file cache at
all, so even a service restart yields "cold `shared_buffers`, warm OS cache" —
which is what the one deliberate cold measurement below is labelled.

**One cold/warm pair, on Q1.** Q1 is the full-partition-scan case, where cache
state matters most. It is measured once with `shared_buffers` cleared by an
elevated service restart, to give the document a single documented cold-vs-warm
contrast to calibrate the other warm-only numbers against.

**Disclosure: an index was added before the baseline run.**
`ix_dim_product_commodity ON dim_product (commodity_desc)` was created *before*
the before-measurements, deliberately. Q3 demonstrates a **non-sargable
predicate** — an index that exists and is reasonable, defeated by wrapping the
column in `upper()`. Without the index in place, Q3 would instead have
demonstrated a missing index, which is a different and far less interesting
failure. The index is an access path Phase 3 uses regardless of this benchmark.

This is stated here rather than left in a commit message because an index added
by the person running the benchmark, undisclosed, reads as rigging the "before"
number even when the reasoning is sound.

**Disclosure: `pg_prewarm` cannot fully warm this machine.** Warm-up runs
`pg_prewarm` over the dimensions and all 126 fact partitions in a fixed order.
But `fact_causal` is 3,193 MB against 1 GB of `shared_buffers` — prewarming it
caches the tail of the read, not the relation. Prewarm makes the starting state
*reproducible*; it does not make it *complete*. Warm numbers for queries
touching `fact_causal` therefore still involve real reads, which the
`shared_read` column shows directly.

**Checkpoint isolation.** `pg_stat_reset_shared('bgwriter')` runs immediately
before each measurement and the counters are read immediately after, giving
per-window counts rather than deltas against a cumulative baseline. (The earlier
checkpoint analysis in this document used cumulative stats spanning a month of
idle time, which is why it could only support a ratio argument.) Any window with
`checkpoints_req > 0` is marked `discarded` with a reason and excluded from the
statistics rather than silently kept. WAL bytes generated per window are
recorded alongside.

**Comparison integrity.** Each measurement stores the query text and its
SHA-256, so a query edited between the before and after runs is detectable
rather than assumed identical. Each also stores a `plan_hash` — a structural
fingerprint over node types, relation names, index names and join strategies,
excluding costs and timings — so a changed plan shape is distinguishable from a
merely faster machine.

**`effective_io_concurrency = 0`**, forced by Windows, applies throughout: no
prefetch on bitmap heap scans. Cold numbers are inflated relative to Linux;
warm numbers are unaffected.

### Mechanism match

Each query predicted a specific plan signature before being written. Whether the
actual plan matched is reported per query below — including where it did not.
A query that turned out to be expensive for a different reason than predicted is
recorded as such, not reshaped until it fits its label.

### Results

Before-run, unoptimized. 5 warm repetitions per query, zero discarded windows
(no requested checkpoint fired during any measurement). One distinct `plan_hash`
and one `query_sha256` per query across all 25 measurements, so the repetitions
are provably like-for-like.

| Query | Median | Min | Max | `shared_read` | `temp_written` | Plan |
|---|---:|---:|---:|---:|---:|---|
| Q1 promo by department | 6,283.8 ms | 4,597.3 | 7,738.2 | 199,903 | 0 | `6cf4a1a3` |
| Q2 basket affinity | 39,535.6 ms | 37,850.5 | 41,432.0 | 48,179 | 85,445 | `164a8d4a` |
| Q3 reorder by commodity | 550.3 ms | 503.7 | 729.5 | 4,530 | 0 | `e1531e91` |
| Q4 promo lift by department | 4,892.4 ms | 4,689.2 | 7,537.3 | 21,373 | 44,460 | `67403ac1` |
| Q5 cohort retention | 8,723.8 ms | 8,528.0 | 9,373.3 | 0 | 6,987 | `8e5baa32` |

### Mechanism match: four of five

| Query | Claimed mechanism | Verdict | Evidence from the recorded plan |
|---|---|---|---|
| Q1 | partition pruning failure | **match** | 102 of 102 `fact_causal` partitions in plan, `Subplans Removed=0` |
| Q2 | sort spill to disk | **match** | `temp_written` 85,445 blocks (~667 MB), sort methods `top-N heapsort`, `external merge` |
| Q3 | index-unusable predicate | **match** | `dim_product` via Seq Scan; `upper()` present in filter |
| Q4 | join-order misestimate | **MISMATCH** | worst genuine error **5.9×** underestimate at a Nested Loop; `fact_causal` hash join 4.0× — both below the 10× threshold set before writing the query |
| Q5 | repeated full-scan aggregation | **match** | 48 scan nodes over 24 distinct `fact_transactions` partitions — a 2.0× repeat factor |

**Q4 is reported as a mismatch rather than adjusted to fit.** It is genuinely
expensive and does spill 44,460 temp blocks, but it is not primarily a
misestimate case.

#### Pre-registered expectation for the Q4 intervention

*Written before `CREATE STATISTICS` was run, and left unedited afterwards.*

The 10× threshold for "this query is a misestimate case" was set before the
query was written. The measured error came in at **5.9×**. Neither the query nor
the intervention is being changed in response to that, because changing either
now would be selecting a result after seeing the data — the same failure this
document argues against elsewhere.

So `CREATE STATISTICS (dependencies, ndistinct)` on the correlated
`fact_causal` columns is applied as planned, and the prediction is recorded
here in advance:

> **Expected: extended statistics will improve the row estimate but will not
> change the join order, and the execution time will be materially unchanged.**
> A 4–6× error is not large enough to have driven the planner to a different
> plan shape, so correcting it should not produce a different one.

The test of that prediction is the **`plan_hash`**, not the clock. If the hash
is unchanged, the plan shape is unchanged and any timing difference is
run-to-run variance — which on this machine reaches 1.83× and would otherwise
be easy to mistake for an improvement.

If that is what happens, it is Entry 2's most useful result rather than a failed
optimization: the right diagnostic tool, correctly aimed at a correctly
identified mechanism, applied to an instance of it too small to matter. Knowing
when a real technique is not worth reaching for is harder-won than knowing the
technique.

Two findings worth noting for the fixes:

- **Q2's spill is in a Sort node, not a HashAggregate** (`HashAgg Batches=0`).
  The `work_mem` intervention targets a sort; `hash_mem_multiplier` would do
  nothing here.
- **Q5 reads zero blocks from outside cache** (`shared_read=0`) — its 8.7s is
  entirely CPU and 6,987 blocks of temp spill, so an index will not help it.

## Entry 2 results — one intervention per query

### Q1 — partition pruning failure → predicate rewrite

**2.09× faster.** Median 4,964.4 ms → 2,378.7 ms. Blocks read fell from 199,903
to 51,207; partitions in the plan from 102 to 27.

#### The first rewrite got faster and did not do what it claimed

This is the clearest argument in this document for recording a plan hash next to
every timing.

The intended fix was to constrain the partition key. The first attempt derived
the week bounds from `dim_week` so the query would survive a change to the
calendar anchor:

```sql
WHERE fc.week_no >= (SELECT min(week_no) FROM dim_week WHERE end_date >= DATE '2015-06-01')
```

| Variant | Plan hash | Median | `shared_read` | Partitions in plan |
|---|---|---:|---:|---:|
| before | `6cf4a1a3` | 4,964.4 ms | 199,903 | **102 of 102** |
| v1 — scalar subquery bounds | `6afea277` | 3,666.6 ms | 96,991 | **102 of 102** |
| v2 — plan-time constants | `a0d03295` | **2,378.7 ms** | 51,207 | **27 of 102** |

**v1 was 1.35× faster and pruned nothing.** A scalar subquery is not evaluated
until execution, so the planner has no value to prune against and must keep every
partition. The speedup came from switching some partitions to index scans on the
primary key's leading `week_no` column — a real improvement, and not the claimed
one.

Reported on timing alone, v1 would have been published as "partition pruning
restored, 1.35× faster". The plan hash changing said *something* was different;
the partition count said it was not pruning.

**v2 uses literal week bounds and prunes to 27 of 102.** The cost is real:
plan-time pruning requires plan-time constants, so callers must resolve a date
window to week numbers *before* the query is planned. That constraint is
documented in the query header rather than discovered later.

#### The `Subplans Removed` correction

The original mechanism check looked for `Subplans Removed > 0`. That counter
reports **execution-time** pruning only. v2's partitions are eliminated at *plan*
time and never enter the plan at all, so `Subplans Removed` stays 0 in the
optimized query — the check would have reported the successful fix as a failure.

**Partition count in the plan is the correct signal**, and the check now uses it.

### Q2 — sort spill → `work_mem` sweep: NO RELIABLE IMPROVEMENT

The baseline spilled 85,444 blocks (~667 MB) in a Sort node. `HashAgg Batches`
was 0, so `hash_mem_multiplier` — the obvious wrong knob — would have done
nothing.

| `work_mem` | Median | Min | Max | `temp_written` | Plan hash |
|---|---:|---:|---:|---:|---|
| 32MB (baseline) | 43,691 ms | 42,769 | 46,324 | 85,444 | `164a8d4a` |
| 64MB | 46,918 ms | 44,152 | **231,129** | 85,384 | `164a8d4a` |
| 128MB | 29,244 ms | 29,212 | 43,862 | 85,363 | `164a8d4a` |
| 256MB | 41,824 ms | 39,947 | 42,916 | 85,353 | `164a8d4a` |
| **512MB** | **56,595 ms** | 39,324 | 60,341 | **0** | `164a8d4a` |

**The intervention worked mechanically and failed at its purpose.** At 512MB the
spill is eliminated entirely — `temp_written` drops from 85,444 blocks to zero —
and median execution time becomes the **worst** in the sweep, 30% slower than the
32MB baseline.

Two things explain it. The machine has 7.7 GB of RAM; 512MB of `work_mem` per sort
node per parallel worker competes with 1 GB of `shared_buffers` and the OS page
cache that, as the cache-state section shows, is doing the real caching work. And
Postgres's external merge sort is efficient — replacing it with a large in-memory
sort under memory pressure is not automatically a win.

**The plan hash is identical across every setting.** `work_mem` changed the
strategy inside the Sort node without changing plan shape at all, which is exactly
the case where timing differences are easiest to over-read.

Run-to-run variance dominates: the 64MB setting produced a 231,129 ms outlier
against its own 44,152 ms minimum — a 5.2× spread *within a single
configuration*. No difference between settings survives that noise except the
spill elimination, which is a counted quantity rather than a timed one.

**Conclusion: `work_mem` is not the fix for this query.** Eliminating the spill is
achievable and does not help. The honest next step is a different intervention —
reducing the 12M candidate pairs the self-join generates — not a larger memory
budget.

### Q3 — non-sargable predicate → expression index: MARGINAL

**Index built in 313 ms.** Median 550.3 ms → 497.5 ms, about 10% — within the
noise band seen elsewhere in this document.

The expression index is correct and demonstrably usable. Against the predicate in
isolation:

```
Bitmap Index Scan on ix_dim_product_commodity_upper
  Index Cond: ((upper(commodity_desc) >= 'SOFT DRINK') AND (upper(commodity_desc) < 'SOFT DRINL'))
```

But **the planner does not choose it in the full query**. It uses the plain
`ix_dim_product_commodity` as a full index scan with the `upper()` predicate as a
filter — a narrower substitute for a sequential scan, not a sargable lookup.

The reason is that `dim_product` was never the bottleneck. It holds 92,353 rows
against `fact_transactions`'s 2,595,732, and the query's cost is dominated by the
fact-table side. Making a cheap access path cheaper does not move a total
dominated by something else.

**A non-sargable predicate is only worth fixing when that predicate is the
bottleneck.** Here the mechanism was correctly identified and correctly fixed, on
a table too small for the fix to matter.

### Q4 — extended statistics: PRE-REGISTERED PREDICTION CONFIRMED

The prediction recorded above, before running: *the estimate will improve, the
join order will not change, execution time will be materially unchanged.*

| | Worst estimate error | Estimated rows | Actual rows |
|---|---:|---:|---:|
| before | 5.9× under | 4,445 | 26,107 |
| after `CREATE STATISTICS` | **5.7× under** | 4,544 | 26,107 |

Median 4,892.4 ms → 4,731.0 ms — a 3% difference inside the noise band.

`CREATE STATISTICS (dependencies, ndistinct)` applied cleanly to the partitioned
parent. It moved the estimate from 4,445 to 4,544 rows against an actual 26,107:
a genuine improvement of about 2% of the error, and nowhere near enough to change
anything.

**The prediction holds.** A 4–6× misestimate was never large enough to have driven
the planner to a different plan shape, so correcting it produced no different
shape. The right diagnostic tool, correctly aimed at a correctly identified
mechanism, applied to an instance of it too small to matter.

This is the most useful result in Entry 2. Knowing when a real technique is not
worth reaching for is harder-won than knowing the technique, and it is only
visible because the expectation was recorded before the measurement.

### Q5 — repeated aggregation → materialized view, and the refresh economics

**The query gets roughly 300,000× faster. That is the least interesting fact about
it.**

| | Value |
|---|---:|
| Query, before | 8,723.8 ms |
| Query, from matview | **0.028 ms** |
| Matview size | 48 kB (164 rows) |
| `REFRESH MATERIALIZED VIEW` | 7,897.0 ms |
| `REFRESH ... CONCURRENTLY` | 7,839.4 ms |

A matview that turns a slow query into a fast one is unremarkable. What decides
whether the system is better is refresh cost against query frequency.

**Break-even is roughly one query per refresh.** A refresh costs 7,897 ms; each
avoided execution saves 8,724 ms. Refreshed daily and queried once a day, the
matview saves nothing. Queried a hundred times a day, it saves about 14 minutes of
cumulative query time daily. The dashboard case — a retention curve loaded on
every page view — sits firmly in the second regime.

**Use `CONCURRENTLY`.** The two modes cost effectively the same (7,839 vs
7,897 ms, a 0.7% difference well inside noise), because duration is dominated by
recomputing the underlying aggregate rather than writing 48 kB. But the
non-concurrent form takes an `ACCESS EXCLUSIVE` lock and blocks every reader for
its whole eight seconds, while `CONCURRENTLY` does not. Same cost, strictly better
availability. It requires a unique index, which `ux_mv_cohort_retention` provides.

**Staleness.** This dataset is static — the panel ended at day 711 — so staleness
is zero regardless of cadence, and the matview is unambiguously correct here. That
is a property of the dataset, not of the technique: against live data, an
eight-second refresh sets the floor on how fresh the retention curve can be, and a
dashboard showing "retention as of 04:00" is making a claim that has to be true.
The refresh timestamp should be surfaced with the numbers rather than implied.

### Summary

| Query | Mechanism | Intervention | Before | After | Verdict |
|---|---|---|---:|---:|---|
| Q1 | partition pruning failure | predicate rewrite | 4,964.4 ms | 2,378.7 ms | **2.09× — worked** |
| Q2 | sort spill | `work_mem` sweep | 43,691 ms | 56,595 ms @512MB | **no reliable gain** |
| Q3 | non-sargable predicate | expression index | 550.3 ms | 497.5 ms | **marginal** |
| Q4 | join-order misestimate | `CREATE STATISTICS` | 4,892.4 ms | 4,731.0 ms | **no change, predicted** |
| Q5 | repeated aggregation | materialized view | 8,723.8 ms | 0.028 ms | **works; refresh decides** |

**One clear win, one marginal, three that did not move the number.** That
distribution is the result, not a disappointing version of one. Every intervention
was chosen for a mechanism identified from a plan before it was applied, and three
of them showed that correctly diagnosing a mechanism does not mean the
corresponding fix is worth applying to that instance of it.

The queries that did not improve are more informative than the one that did. Q1's
win came from making a filter reach the partition key — a structural property of
the query. Q2, Q3 and Q4 tried to make an existing plan cheaper and found the plan
was not where the cost was.
