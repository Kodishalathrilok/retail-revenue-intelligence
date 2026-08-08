# Methodology notes

Three decisions in this project were made wrongly first and corrected against
evidence. They are written up here because the corrections are more informative
than the final answers: each one would have produced a plausible-looking result
that was quietly wrong, and each was caught by a specific measurement rather
than by review.

---

## 1. The control group was defined too strictly

**The decision.** Phase 6c estimates the effect of a dunnhumby marketing
campaign using difference-in-differences. That requires a control group of
untreated households.

**What was wrong.** The first definition was *households enrolled in no campaign
at all*. This is intuitive and it is what "untreated" sounds like it should
mean. It is also wrong, and it fails in a way that is easy to miss: it produces
no error, only a smaller number.

The failure was found by a smoke test rather than by reasoning. Before the real
data arrived, the probe was run against a synthetic dataset shaped like
dunnhumby, deliberately seeded with defects — including one blanket campaign
enrolling every household in the panel. Under the strict definition, that single
campaign emptied the control group for *every other campaign in the dataset*.
The probe reported that no difference-in-differences was possible anywhere.

That conclusion was false. A campaign that ran on days 1–100 does not
contaminate a comparison of days 420–500. Treatment status only matters if it
overlaps the window being analysed.

**The correction.** The control group is now households not in this campaign and
not in any campaign whose window overlaps this one's *analysis window* — a
bounded 180-day pre-period through campaign end. Campaigns running at unrelated
times no longer disqualify anyone.

The stricter never-treated count is still computed and reported as
`strict_control_n`, because where it is non-empty it is the cleanest group
available. It simply does not drive the verdict.

**Why it matters.** On the real data, 916 households are in no campaign at all,
so the strict definition would have survived — the error would never have
surfaced, and every control group would have been silently and unnecessarily
narrowed from ~2,000 households to 916. The estimate would have been noisier for
no methodological gain, and nothing would have indicated why.

---

## 2. Twenty-one viable campaigns were actually three

**The decision.** Which campaigns can support an honest causal estimate.

**What was wrong.** The first real run reported 21 of 30 campaigns as viable. The
screen checked pre-period depth, post-period depth, treatment size and control
size. It also *computed* a contamination figure — the share of treated
households simultaneously enrolled in another overlapping campaign — and then
did nothing with it.

The numbers it was ignoring:

| Campaign | Enrolled | Contaminated | Verdict as first reported |
|---|---:|---:|---|
| 18 | 1,133 | 90.8% | viable |
| 17 | 202 | 100.0% | viable |
| 22 | 276 | 98.2% | viable |

Campaign 17 was marked viable with **every single treated household**
simultaneously enrolled in something else. An estimate on that group does not
measure campaign 17. It measures campaign 17 bundled with whatever else those
households were receiving, and reports the total as if it were the part.

**The correction.** Contamination is not treated as a rejection criterion —
that would discard usable data. Instead, contaminated households are removed
from the treatment group, and the size screen is applied to what remains, since
that is the group the estimator would actually use.

Viable campaigns fell from 21 to 3:

| Campaign | Type | Enrolled | Uncontaminated | Contamination | Control |
|---|---|---:|---:|---:|---:|
| **26** | TypeA | 332 | **310** | **6.6%** | 2,165 |
| 8 | TypeA | 1,076 | 432 | 59.9% | 1,202 |
| 18 | TypeA | 1,133 | 104 | 90.8% | 987 |

Ranking also changed from raw enrolment to contamination, because enrolment is
the misleading number here. Campaign 18 is the largest campaign in the dataset
and among the worst candidates; campaign 26 is a third its size and by far the
best.

**Why it matters.** Sorting by enrolment — the obvious choice — puts the single
worst campaign at the top of the list. A reasonable person builds Phase 6c on
campaign 18, gets a clean-looking estimate with a large sample, and reports an
effect that is mostly other campaigns.

---

## 3. The calendar anchor contradicted 694,450 rows

**The decision.** dunnhumby records time as `DAY` 1–711 relative to an
unpublished panel start. `dim_date` needs real calendar dates, so the panel has
to be anchored somewhere.

**What was wrong.** The plan was to anchor `DAY 1` to a Monday, so that derived
weeks would run Monday–Sunday like retail weeks do. This was stated in the
README before the data was examined, with the reasoning that the choice was
arbitrary and any Monday would do.

It was not arbitrary. dunnhumby ships its own `WEEK_NO` column, and
`causal_data` — 36.8 million rows of promotional exposure — is keyed on it. Any
anchor that disagrees with `WEEK_NO` silently misaligns every join between
transactions and promotions.

The probe tested whether `WEEK_NO` was derivable from `DAY`, assuming
`ceil(day / 7)`. **694,450 of 2,595,732 transactions disagreed** — 26.8%.

**The correction.** Inspecting the actual day span of each week showed the panel
does not begin on a week boundary:

| week_no | day range | days |
|---:|---|---:|
| 1 | 1–5 | **5** |
| 2 | 6–12 | 7 |
| 3 | 13–19 | 7 |

Week 1 is a partial five-day week. The exact rule is `week_no = (day + 8) // 7`,
which matches on all 2,595,732 rows with zero exceptions.

So day 6 opens the first full week and is the Monday anchor, which makes
**day 1 a Wednesday**. The original plan was off by two days.

**Why it matters.** This is the most dangerous of the three, because nothing
would have failed. Anchoring day 1 to a Monday produces a `dim_date` that looks
entirely correct — 711 consecutive dates, sensible weeks, valid months. Joins to
`causal_data` would succeed and return rows. They would just be the wrong rows,
shifted by two days, for every promotional analysis in the project.

Because of this, a load-time assertion recomputes `week_no` from `day` on every
transaction row and fails the load if a single row disagrees. The rule is now
enforced rather than assumed.

---

## What generalises

All three errors share a shape: each produced output that looked correct.
Nothing crashed, nothing was empty, no test failed. The strict control group
returned a plausible number, the 21 viable campaigns were a plausible finding,
and a Monday-anchored calendar is a plausible calendar.

Two things caught them, and neither was code review:

- **A synthetic dataset with deliberately seeded defects**, built because the
  real data had not arrived yet. It was constructed to include a blanket
  campaign, and that is the only reason the control-group flaw surfaced at all.
- **Checking a derived value against its source rather than trusting the
  derivation.** `week_no` was recomputed and compared. Had it merely been
  derived and used, the mismatch would never have been visible.

Both are now permanent: the synthetic fixtures back the test suite, and the
`week_no` check runs on every load.
