# Dataset profile -- dunnhumby *The Complete Journey*

Generated 2026-08-07 12:48 UTC by `rrip profile` from a real run over the raw CSVs. Every number below is measured, not estimated.

## File inventory

| File | Path | Data rows |
|---|---|---:|
| `campaign_desc` | `campaign_desc.csv` | 30 |
| `campaign_members` | `campaign_table.csv` | 7,208 |
| `causal` | `causal_data.csv` | 36,786,524 |
| `coupon_redemptions` | `coupon_redempt.csv` | 2,318 |
| `coupons` | `coupon.csv` | 124,548 |
| `households` | `hh_demographic.csv` | 801 |
| `products` | `product.csv` | 92,353 |
| `transactions` | `transaction_data.csv` | 2,595,732 |

## causal_data scale

- **Rows:** 36,786,524
- Week range: 9-101
- Distinct products: 68,377
- Distinct stores: 115
- `display` values: ['0', '1', '2', '3', '4', '5', '6', '7', '9', 'A']
- `mailer` values: ['0', 'A', 'C', 'D', 'F', 'H', 'J', 'L', 'P', 'X', 'Z']

## Data quality findings

| Check | Value | Why it matters |
|---|---|---|
| transaction rows | 2,595,732 | fact_transactions grain candidate |
| distinct households | 2,500 | dim_household size |
| distinct baskets | 276,484 | degenerate dimension |
| distinct products in tx | 92,339 | dim_product usage |
| distinct stores | 582 | dim_store size |
| day range | 1 - 711 | dim_date span |
| week range | 1 - 102 | partition range for fact_causal |
| week_no != ceil(day/7) [naive] | 694,450 | non-zero means the panel does not start on a week boundary |
| week_no != (day+8)//7 [offset] | 0 | 0 confirms week 1 is partial and day 6 starts the first full week |
| week 1 day span | 1-5 | a short week 1 fixes the weekday anchor: day 6 is a Monday, so day 1 is a Wednesday |
| duplicate (basket_id, product_id) | 0 | non-zero forces a surrogate key; do NOT dedupe real transactions |
| sales_value < 0 | 0 | returns/refunds; must not be silently dropped |
| sales_value == 0 | 18,850 | free goods / full coupon coverage |
| retail_disc sign | 1,303,026 neg / 36 pos | establishes whether gross = sales_value - retail_disc |
| quantity <= 0 | 14,466 | returns or data errors |
| quantity > 1000 | 23,101 | weighted goods recorded in grams, not units |
| tx products missing from product.csv | 0 | referential integrity; non-zero needs an unknown-member row in dim_product |
| households with demographics | 801 / 2500 (32.0%) | limits which confounders Phase 6c can adjust for |
| transactions covered by demographics | 55.0% | demographic slices cover only part of the panel |

## Campaign viability for difference-in-differences

Screens: pre-period >= 90d, post-period >= 14d, treated >= 100, control >= 100 households.

The analysis window is a bounded 180-day pre-period through campaign end.

- **treated_n** -- households enrolled in this campaign.
- **clean_treated_n** -- those *not* also enrolled in an overlapping campaign. This is the treatment group Phase 6c would use, and the one the size screen applies to: an estimate on the full enrolled set measures this campaign plus whatever else those households were in.
- **control_n** -- households not in this campaign and not in any campaign overlapping its analysis window.
- **strict_control_n** -- households in *no* campaign at all. Cleaner where it exists, but a single blanket campaign can empty it without invalidating the comparison, so it does not drive the verdict.

Household reach by campaign type (vs 2,500 panel households): **TypeA** 1,513 (61%), **TypeB** 1,023 (41%), **TypeC** 397 (16%)

| campaign | type | start_day | end_day | pre_days | post_days | treated_n | clean_treated_n | contaminated_pct | control_n | strict_control_n | overlapping_campaigns | pre_weeks | slope_gap | viable | reasons |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 26 | TypeA | 224 | 264 | 180 | 41 | 332 | 310 | 6.6 | 2165 | 916 | 27,28 | 27 | 0.8499 | True | passes all screens |
| 8 | TypeA | 412 | 460 | 180 | 49 | 1076 | 432 | 59.9 | 1202 | 916 | 1,2,3,4,5,6,7,9,26,27,28,29,30 | 26 | 0.2435 | True | passes all screens |
| 18 | TypeA | 587 | 642 | 180 | 56 | 1133 | 104 | 90.8 | 987 | 916 | 3,5,6,7,8,9,10,11,12,13,14,15,16,17,19,20,21,22 | 26 | 0.4995 | True | passes all screens |
| 29 | TypeB | 281 | 334 | 180 | 54 | 118 | 77 | 34.7 | 1965 | 916 | 26,27,28,30 | 27 | 0.134 | False | uncontaminated treated n=77 < 100 (of 118 enrolled) |
| 5 | TypeB | 377 | 411 | 180 | 35 | 166 | 62 | 62.7 | 1701 | 916 | 1,2,3,4,6,7,26,27,28,29,30 | 26 | 0.1519 | False | uncontaminated treated n=62 < 100 (of 166 enrolled) |
| 4 | TypeB | 372 | 404 | 180 | 33 | 81 | 30 | 63.0 | 1701 | 916 | 1,2,3,5,6,7,26,27,28,29,30 | 27 | 0.33 | False | uncontaminated treated n=30 < 100 (of 81 enrolled) |
| 30 | TypeA | 323 | 369 | 180 | 47 | 361 | 96 | 73.4 | 1953 | 916 | 1,2,3,26,27,28,29 | 27 | 0.0729 | False | uncontaminated treated n=96 < 100 (of 361 enrolled) |
| 2 | TypeB | 351 | 383 | 180 | 33 | 48 | 8 | 83.3 | 1804 | 916 | 1,3,4,5,26,27,28,29,30 | 27 | 0.115 | False | uncontaminated treated n=8 < 100 (of 48 enrolled) |
| 1 | TypeB | 346 | 383 | 180 | 38 | 13 | 2 | 84.6 | 1804 | 916 | 2,3,4,5,26,27,28,29,30 | 24 | 0.4882 | False | uncontaminated treated n=2 < 100 (of 13 enrolled) |
| 9 | TypeB | 435 | 467 | 180 | 33 | 176 | 22 | 87.5 | 1193 | 916 | 1,2,3,4,5,6,7,8,10,26,27,28,29,30 | 27 | 0.7939 | False | uncontaminated treated n=22 < 100 (of 176 enrolled) |
| 28 | TypeB | 259 | 320 | 180 | 62 | 17 | 2 | 88.2 | 2078 | 916 | 26,27,29 | 26 | 0.4675 | False | uncontaminated treated n=2 < 100 (of 17 enrolled) |
| 19 | TypeB | 603 | 635 | 180 | 33 | 130 | 13 | 90.0 | 989 | 916 | 6,7,8,9,10,11,12,13,14,15,16,17,18,20,21,22 | 27 | 0.3761 | False | uncontaminated treated n=13 < 100 (of 130 enrolled) |
| 13 | TypeA | 504 | 551 | 180 | 48 | 1077 | 97 | 91.0 | 1097 | 916 | 1,2,3,4,5,6,7,8,9,10,11,12,14,15,29,30 | 27 | 0.0859 | False | uncontaminated treated n=97 < 100 (of 1077 enrolled) |
| 27 | TypeC | 237 | 300 | 180 | 64 | 12 | 1 | 91.7 | 2078 | 916 | 26,28,29 | 24 | 0.4055 | False | uncontaminated treated n=1 < 100 (of 12 enrolled) |
| 7 | TypeB | 398 | 432 | 180 | 35 | 198 | 14 | 92.9 | 1225 | 916 | 1,2,3,4,5,6,8,26,27,28,29,30 | 26 | 1.2894 | False | uncontaminated treated n=14 < 100 (of 198 enrolled) |
| 24 | TypeB | 659 | 719 | 180 | 61 | 100 | 6 | 94.0 | 1093 | 916 | 10,11,12,13,14,15,16,17,18,19,20,21,22,23,25 | 27 | 0.7675 | False | uncontaminated treated n=6 < 100 (of 100 enrolled) |
| 10 | TypeB | 463 | 495 | 180 | 33 | 123 | 5 | 95.9 | 1203 | 916 | 1,2,3,4,5,6,7,8,9,11,12,27,28,29,30 | 25 | 0.6884 | False | uncontaminated treated n=5 < 100 (of 123 enrolled) |
| 16 | TypeB | 561 | 593 | 180 | 33 | 188 | 6 | 96.8 | 1011 | 916 | 1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,17,18 | 26 | 0.0597 | False | uncontaminated treated n=6 < 100 (of 188 enrolled) |
| 20 | TypeC | 615 | 685 | 180 | 71 | 244 | 6 | 97.5 | 983 | 916 | 8,9,10,11,12,13,14,15,16,17,18,19,21,22,23,24,25 | 24 | 0.3267 | False | uncontaminated treated n=6 < 100 (of 244 enrolled) |
| 23 | TypeB | 646 | 684 | 180 | 39 | 183 | 4 | 97.8 | 1079 | 916 | 9,10,11,12,13,14,15,16,17,18,19,20,21,22,24,25 | 27 | 0.6385 | False | uncontaminated treated n=4 < 100 (of 183 enrolled) |
| 22 | TypeB | 624 | 656 | 180 | 33 | 276 | 5 | 98.2 | 989 | 916 | 8,9,10,11,12,13,14,15,16,17,18,19,20,21,23 | 27 | 0.8072 | False | uncontaminated treated n=5 < 100 (of 276 enrolled) |
| 12 | TypeB | 477 | 509 | 180 | 33 | 170 | 3 | 98.2 | 1099 | 916 | 1,2,3,4,5,6,7,8,9,10,11,13,27,28,29,30 | 25 | 0.9812 | False | uncontaminated treated n=3 < 100 (of 170 enrolled) |
| 6 | TypeC | 393 | 425 | 180 | 33 | 65 | 1 | 98.5 | 1225 | 916 | 1,2,3,4,5,7,8,26,27,28,29,30 | 25 | 0.7528 | False | uncontaminated treated n=1 < 100 (of 65 enrolled) |
| 11 | TypeB | 477 | 523 | 180 | 47 | 214 | 3 | 98.6 | 1099 | 916 | 1,2,3,4,5,6,7,8,9,10,12,13,27,28,29,30 | 26 | 0.7535 | False | uncontaminated treated n=3 < 100 (of 214 enrolled) |
| 14 | TypeC | 531 | 596 | 180 | 66 | 224 | 2 | 99.1 | 981 | 916 | 1,2,3,4,5,6,7,8,9,10,11,12,13,15,16,17,18,30 | 18 | 1.4648 | False | uncontaminated treated n=2 < 100 (of 224 enrolled) |
| 25 | TypeB | 659 | 691 | 180 | 33 | 187 | 1 | 99.5 | 1093 | 916 | 10,11,12,13,14,15,16,17,18,19,20,21,22,23,24 | 13 | 1.246 | False | uncontaminated treated n=1 < 100 (of 187 enrolled) |
| 15 | TypeC | 547 | 708 | 180 | 162 | 17 | 0 | 100.0 | 948 | 916 | 1,2,3,4,5,6,7,8,9,10,11,12,13,14,16,17,18,19,20,21,22,23,24,25,30 | 0 | nan | False | uncontaminated treated n=0 < 100 (of 17 enrolled); no pre-period transactions in one group |
| 21 | TypeB | 624 | 656 | 180 | 33 | 65 | 0 | 100.0 | 989 | 916 | 8,9,10,11,12,13,14,15,16,17,18,19,20,22,23 | 0 | nan | False | uncontaminated treated n=0 < 100 (of 65 enrolled); no pre-period transactions in one group |
| 17 | TypeB | 575 | 607 | 180 | 33 | 202 | 0 | 100.0 | 999 | 916 | 3,4,5,6,7,8,9,10,11,12,13,14,15,16,18,19 | 0 | nan | False | uncontaminated treated n=0 < 100 (of 202 enrolled); no pre-period transactions in one group |
| 3 | TypeC | 356 | 412 | 180 | 57 | 12 | 0 | 100.0 | 1225 | 916 | 1,2,4,5,6,7,8,26,27,28,29,30 | 0 | nan | False | uncontaminated treated n=0 < 100 (of 12 enrolled); no pre-period transactions in one group |

## Verdict

**3 campaign(s) viable.** Cleanest candidate: campaign 26 (TypeA) -- 180d pre-period, 310 uncontaminated treated (of 332 enrolled, 6.6% contaminated) vs 2165 control.

`slope_gap` is the difference in pre-period weekly-spend trend between groups. It is a smell test, not a parallel-trends test -- Phase 6c must still run the formal check and report violations.

---

## Corrections found after profiling

Two findings below were **missed by this probe** and surfaced later, during the
Phase 1 load. They are recorded here so the profile is not read as complete.

### causal_data has 15,245 duplicate natural keys

`causal_data` contains **15,245 duplicate `(week_no, product_id, store_id)`
rows** out of 36,786,524. `fact_causal` deduplicates on load via `DISTINCT ON`,
so it holds 36,771,279 rows.

The probe verified `(basket_id, product_id)` uniqueness on transactions and
confirmed zero duplicates there — then never ran the equivalent check on
`causal_data`. The uniqueness screen was applied to one fact source and not the
other, so a real data quality issue reached the loader instead of the profile.

Reconciliation accounts for this explicitly: `fact_causal`'s expected row count
is source lines minus the recorded duplicate count, read from
`etl_data_quality`. Comparing raw line counts would report a permanent false
failure on a documented condition.

### The money anomaly counts above were distorted by float32

This probe cast `sales_value` and `retail_disc` to `float32` for memory
efficiency. Float32 carries roughly 7 significant digits, which puts values near
zero on the wrong side of a comparison. The same checks run against
`NUMERIC(10,2)` after load give different answers:

| Check | This probe (float32) | Loaded (NUMERIC(10,2)) |
|---|---:|---:|
| `retail_disc > 0` | 36 | **10** |
| `sales_value - retail_disc < 0` | 17 | **1** |
| `sales_value = 0` | 18,850 | **18,879** |
| `quantity <= 0` | 14,466 | 14,466 |
| `quantity > 1000` | 23,101 | 23,101 |
| `sales_value < 0` | 0 | 0 |

**The NUMERIC figures are correct.** Counts on quantity — an integer column —
were unaffected, which is why the discrepancy is confined to the money columns.

The Phase 4 assertion suite reads the recorded values from `etl_data_quality`,
measured at load against exact decimals, not the float32 figures above.
