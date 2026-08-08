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

Three practices come out of this:

1. **Confirm AC power and a zero standby timeout before any timed run.** On a
   laptop, an unattended benchmark measures the power policy as much as the
   query.
2. **Report the distribution, not the total.** A mean hides a 90× outlier
   completely, and the total merely looks disappointing rather than obviously
   wrong. Median, max and outlier count would have exposed this immediately.
3. **Do not filter outliers on a threshold picked before seeing the
   distribution.** "Batches over 100 seconds" found three of the four. The
   fourth was found by asking what the maximum was once the known outliers were
   removed — which is a question with no threshold in it.

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

Not yet run. Five deliberately expensive analytical queries with
`EXPLAIN (ANALYZE, BUFFERS)` captured before and after indexing and
materialized views, reported cold and warm.
