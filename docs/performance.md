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

| | Original | Corrected |
|---|---:|---:|
| Rows | 36,771,279 | 36,771,279 |
| Median batch | 14.1s | **10.1s** |
| Median throughput | 27,518 rows/s | **37,827 rows/s** |
| Max batch | 1,322.3s | **12.1s** |
| Batches > 100s | 3 | **0** |
| Total (wall clock) | 4,513.4s (75.2 min) | 945.5s (15.8 min) |
| **Total (comparable)** | **~1,400s (23.3 min)** | **945.5s (15.8 min)** |
| **Speedup** | | **~1.48×** |

**The wall-clock rows are not a fair comparison, and the reason matters — see
below.** The honest figure is **1.48×**, not the 4.8× that 75.2 → 15.8 implies.

Post-load steps, corrected run: foreign keys 58.2s, indexes including the
deferred `fact_causal` PK 189.8s.

### Why the totals are not comparable: the distribution, not the mean

Reporting only totals would have hidden the most important thing in this run.

Original-ordering batch distribution across 93 batches:

| Statistic | Value |
|---|---:|
| Median | 14.1s |
| Max | **1,322.3s** |
| Batches over 100s | 3 |

**Three batches consumed 3,159s of the 4,513s total — 70%.** At the median rate,
the whole table loads in roughly 22 minutes. The mean is meaningless here; the
distribution is the finding.

Those three batches were not slow for any database reason:

- **Not data volume.** They averaged 403,129 rows against 395,132 for normal
  batches — a 2% difference. Normal batches ran up to 589,473 rows in 15s.
- **Not checkpoint pressure.** `checkpoints_req` was 27 against
  `checkpoints_timed` 2,597 — about 1%. `max_wal_size` at 8GB was not the
  constraint. (Caveat: `pg_stat_bgwriter` is cumulative since 2026-07-07 and was
  not snapshotted around the load window, so this is a ratio argument, not an
  isolated measurement. The lopsidedness makes it conclusive regardless.)
- **Not antivirus or cloud sync.** OneDrive was not running; Defender's last
  scan was two days before the load window.

**They were the laptop entering Modern Standby.** Windows Kernel-Power events
506/507 map onto each stalled batch almost exactly:

| Batch | Started | Duration | Standby window | Standby duration |
|---|---|---:|---|---:|
| w32 | 12:30:26 | 22m 02s | 12:30:18 → 12:52:22 | 22m 04s |
| w59 | 12:59:14 | 12m 50s | 12:59:09 → 13:11:51 | 12m 42s |
| w99 | 13:22:16 | 17m 47s | 13:21:10 → 13:40:04 | 18m 54s |

The machine idled into standby whenever no interactive commands were running,
suspending the load. The corrected run stayed awake throughout and recorded
**zero** batches over 100s, with a maximum of 12.1s.

So the corrected run is faster for two independent reasons, and only one is an
optimization. Deferring the PK and dropping `ON CONFLICT` is worth **1.48×**.
The rest of the apparent 4.8× is a sleeping laptop.

**Before running any unattended benchmark on this machine**, disable standby —
otherwise the measurement includes sleep cycles:

```
powercfg /change standby-timeout-ac 0
```

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
