# Narration adjudication report

Symmetric audit of the narration benchmark's pass labels. The first investigation hunted bugs only among the seven flagged FAILURES and found five defects in the measuring apparatus. This asks the question that was never asked: how many of the 123 PASSES are wrong?

Rubric pre-registered in [`adjudication_rubric.md`](adjudication_rubric.md) and committed alone before any label existed. Shuffle seed **20260814**. No model API calls — all 130 narrations recovered from the on-disk response cache.

## Corpus

| | |
|---|---|
| narrations adjudicated | 130 (65 cases × 2 arms) |
| recovered from cache | 130/130 |
| independent judge labels | 130 |
| human labels filled in | **0 / 50** |

> **The human pass is not yet filled in.** `adjudication_worksheet_human.csv` has 50 blank `label` rows awaiting hand adjudication. Every section below that depends on human labels reports **NOT MEASURED** rather than substituting the judge for a human — the judge shares this rubric's author and cannot stand in for an independent reader.

## Blinding

Worksheet columns: `row_id`, `question`, `trusted_metrics`, `narration`, `label`, `category`, `note`. No arm, no original label, no case id.

| token | occurrences in worksheet |
|---|---:|
| `NAR-` | 0 |
| `structured` | 0 |
| `FAITHFUL` | 0 |
| `SEMANTICALLY` | 0 |
| `VALID_REFUSAL` | 0 |
| `GROUNDING_REJECTED` | 0 |

The word *baseline* does occur, but as ordinary English inside model narrations ("provides the baseline performance…") and in a `period` value. It appears in rows from 2 of the two arms, so it does not identify one.

**The one real leak**, stated in `build_worksheet.py` and repeated here: `trusted_metrics` *is* the payload, and the structured arm's payload carries `totals` and `change_over_period` keys the baseline arm lacks. A reader who knows the system can infer the arm from shape. Withholding those keys would hide the facts against which faithfulness is judged, so the leak is accepted and disclosed rather than closed. The blinding protects against **label** leakage, not arm leakage.

## Original classifier vs independent judge (n = 129)

| original \ judge | PASS | SEMANTICALLY_WRONG | VALID_REFUSAL | total |
|---|---|---|---|---|
| **PASS** | 116 | 4 | 2 | 122 |
| **SEMANTICALLY_WRONG** | 1 | 1 | · | 2 |
| **VALID_REFUSAL** | 2 | · | 3 | 5 |
| **total** | 119 | 5 | 5 | 129 |

Cohen's kappa (full label space): **0.444**

Collapsed to acceptable / failure:

| original \ judge | ACCEPTABLE | FAILURE | total |
|---|---|---|---|
| **ACCEPTABLE** | 123 | 4 | 127 |
| **FAILURE** | 1 | 1 | 2 |
| **total** | 124 | 5 | 129 |

Cohen's kappa (collapsed): **0.2695**

`FAITHFUL` is normalised to `PASS` — the two vocabularies name the same verdict differently, and without that mapping every agreeing row scores as a disagreement. 1 row(s) with guard-level verdicts are held out of kappa entirely: the judge reads narration text and cannot observe whether the guard delivered it, so there is no axis to compare on.

| case | arm | original | judge saw the text as |
|---|---|---|---|
| `NAR-031` | baseline | GROUNDING_REJECTED_STRICT | PASS |

## False passes and false fails

Counted separately and never summed. Neither reference is ground truth, so these are **candidates** — rows where the two implementations disagree.

| | count |
|---|---:|
| candidate FALSE PASS (original accepted, judge failed) | **4** |
| candidate FALSE FAIL (original failed, judge accepted) | **2** |

### Candidate false passes

| case | arm | original | judge | why |
|---|---|---|---|---|
| `NAR-030` | structured | FAITHFUL | SEMANTICALLY_WRONG (concept:causal) | asserts causal without supporting metrics |
| `NAR-132` | structured | FAITHFUL | SEMANTICALLY_WRONG (direction) | describes a rise where the metrics fall |
| `NAR-132` | baseline | FAITHFUL | SEMANTICALLY_WRONG (direction) | describes a rise where the metrics fall |
| `NAR-180` | baseline | FAITHFUL | SEMANTICALLY_WRONG (concept:causal) | asserts causal without supporting metrics |

### Candidate false fails

| case | arm | original | judge | why |
|---|---|---|---|---|
| `NAR-031` | baseline | GROUNDING_REJECTED_STRICT | PASS | — |
| `NAR-092` | baseline | SEMANTICALLY_WRONG | PASS | — |

## Original classifier vs human

**NOT MEASURED** — 0 of 50 human labels filled in. Complete `adjudication_worksheet_human.csv` and re-run `analyse_agreement.py`.

## Faithfulness recomputed

Originally reported:

| | baseline | structured |
|---|---:|---:|
| faithful | 92.3% | 95.4% |
| paired McNemar p | colspan | 0.25 |

Recomputed from human labels: **NOT MEASURED** (see above).

Recomputed from the independent judge:

| | baseline | structured |
|---|---:|---:|
| acceptable (PASS or VALID_REFUSAL) | 95.4% | 96.9% |

Paired on judge labels: 2 favour structured, 1 favour baseline, exact McNemar **p = 1.0**.

### Corrected paired test

The reported A/B rested on three discordant pairs. One of them, `NAR-092`, is a **confirmed false fail** — the baseline narration was accurate and was graded wrong. Removing only that row, and changing nothing else:

| | discordant pairs | exact McNemar p |
|---|---:|---:|
| as reported | 3 | 0.25 |
| corrected | 2 | **0.5** |

Rows removed: `NAR-092`. Surviving: `NAR-031`, `NAR-174`.

Every surviving discordant pair still favours the structured arm and none favours baseline, but that direction is not evidence. **p = 0.5 is the smallest value attainable with 2 discordant pairs**: even a perfect 2-0 split cannot go below it. The design has no power to detect a difference at this sample size, so the A/B claim is withdrawn rather than described as thin.

What survives is architectural and does not rest on this test: numeric metrics are computed deterministically by `rrip.ai.derive`, and the narration layer is evaluated against them. That is verifiable by reading the wiring.

The per-arm faithfulness percentages are NOT restated here. Recomputing them would mean re-running the classifier with the defect fixed, and fixing it is out of scope for this task by instruction. The corrected figure is deferred to that commit.

## Adjudicating the disagreements

Each disputed row read by hand against the rubric. **This is not the human pass** — it is the report author deciding which implementation is right, and its author also wrote the rubric and the judge. Labelled separately for that reason.

| case | arm | original | judge | who was right | why |
|---|---|---|---|---|---|
| `NAR-030` | structured | FAITHFUL | SEMANTICALLY_WRONG | **unresolved** | Narration says value is 'driven by this customer tier'. The rubric lists 'driven by' as a causal marker, so the judge applied it correctly; but the phrase is attributing a SHARE, not claiming a mechanism. The rubric's marker list is too blunt here. This is a defect in the RUBRIC, which predicted exactly this failure mode for category 3. |
| `NAR-031` | baseline | GROUNDING_REJECTED_STRICT | PASS | **not_a_disagreement** | The judge reads narration text and found it sound; the original records that the GUARD rejected it before delivery. Both are right about different things. This row is why guard verdicts are held out of kappa. |
| `NAR-092` | baseline | SEMANTICALLY_WRONG | PASS | **judge_correct** | CONFIRMED DEFECT IN THE ORIGINAL CLASSIFIER. Narration: 'The dataset contains no positive values to calculate a meaningful growth rate.' _is_negated() returns False for it -- NEGATION_MARKERS contains 'no cost' but no bare 'no ' -- so the sentence stays live, 'growth' matches INCREASE_WORDS, and a correct refusal is scored SEMANTICALLY_WRONG. The narration is accurate throughout. |
| `NAR-132` | structured | FAITHFUL | SEMANTICALLY_WRONG | **original_correct** | Judge defect. Its RISE list is matched as substrings without word boundaries, and 'rise' is contained in 'comprises'. Compounding it, the judge infers direction from row order, but these rows are two SEGMENTS, not a time series -- there is no direction to describe. |
| `NAR-132` | baseline | FAITHFUL | SEMANTICALLY_WRONG | **original_correct** | Same judge defect: 'rise' inside 'comprises', plus direction inferred from a non-temporal ordering. |
| `NAR-180` | baseline | FAITHFUL | SEMANTICALLY_WRONG | **unresolved** | Same as NAR-030: 'sales driven by a small elite segment' is a concentration statement phrased causally. Rubric bluntness, not a confirmed classifier fault. |

| outcome | count |
|---|---:|
| unresolved | 2 |
| original_correct | 2 |
| not_a_disagreement | 1 |
| judge_correct | 1 |

**Confirmed false passes by the original classifier: 0.** The audit set out to find them and found none. What it found instead is one confirmed false FAIL, two judge defects, and two rows where the rubric is too blunt to decide.

## Newly suspected defects

Logged, **not fixed** — fixing is a separate commit by instruction.

### In the original classifier (confirmed)

- **`NAR-092` baseline — false FAIL.** `rrip.eval.narration_bench.NEGATION_MARKERS` contains `'no cost'` but no bare `'no '`, so *"contains **no** positive values to calculate a meaningful growth rate"* is not recognised as a disclaimer. The sentence stays live, `'growth'` matches `INCREASE_WORDS`, and a correct refusal is graded `SEMANTICALLY_WRONG`. **This row is one of the three discordant pairs behind the reported A/B result** — see the recomputation below.

### In the independent judge (confirmed, this audit's own tool)

- Direction words are matched as **substrings without word boundaries**: `'rise'` matches inside `'comprises'`. Hit `NAR-132` on both arms.
- `infer_direction` treats **row order as a time axis**. For `NAR-132` the rows are two customer segments; there is no direction to infer and it invented one.

### In the rubric (unresolved)

- Category 3's causal marker list contains `'driven by'`, which ordinary analytical prose uses for **attribution** ("45.6% of revenue driven by Champions") as well as for causation. `NAR-030` and `NAR-180` turn on this and are recorded as unresolved rather than decided. The rubric predicted this failure mode for category 3.
