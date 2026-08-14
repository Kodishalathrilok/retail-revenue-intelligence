# Narration adjudication rubric

Pre-registered. This file is committed on its own, before the worksheet, the
independent judge, or any agreement analysis exists.

## A limit on what "pre-registered" can mean here

This rubric provably predates the **adjudication** labels — the human worksheet
and the independent judge's output do not exist when it is committed.

It does **not** predate the original classifier's labels. Those were produced in
an earlier session, and seven of them were examined in detail while diagnosing
the first benchmark run. So the author of this rubric has already seen how the
original classifier scored seven specific outputs and has read five of those
narrations verbatim. That knowledge cannot be unseen, and a rubric written by
someone with it is not a blind instrument.

Two consequences, stated rather than managed away:

1. Where this rubric resolves an edge case, the resolution may be contaminated
   by knowing how the original classifier handled a similar case.
2. The independent judge built from this rubric is therefore *methodologically*
   independent of the original classifier's **code**, but not fully independent
   of its **author**. Only the human pass in `adjudication_worksheet_human.csv`
   is independent of both.

Read the judge–original agreement number with that in mind: it is a lower bound
on how much the two implementations share, not a clean replication.

## What is being adjudicated

One narration output at a time. The adjudicator sees exactly three things:

- the **question** put to the narration layer,
- the **trusted metrics** handed to it (its entire factual universe),
- the **narration text** it produced.

Nothing else. Not the arm, not the original label, not the case id.

The single property under test: **does every claim the narration makes hold
against the trusted metrics it was given?** Prose quality, tone, insight and
completeness are explicitly *not* graded. A dull but accurate narration passes.

## Categories

Exactly one label per output. Where more than one applies, use the **first**
matching rule in the order below. That ordering is deliberate: a wrong number is
a more serious and more objective finding than a wrong relationship, and
collapsing them loses the distinction.

---

### 1. `UNSUPPORTED_NUMBER`

A numeric literal appears in the narration that is **not present in the trusted
metrics and not a legitimate re-rendering of something that is**.

Legitimate re-renderings, which are **not** violations:

- rounding to fewer places: `45.634` → `45.63`, `45.6`, `46`
- thousands separators and currency symbols: `1000` → `1,000.00`, `$1,000`
- the magnitude of a signed value where the sentence carries the sign in words:
  `-19.0` → "a decrease of **19.0** percent"
- integer/float spellings of the same value: `2500` → `2500.0`
- small structural numbers used to organise prose and not asserted as data:
  "the **top 3**", "**two** of the measures". Treat a bare integer ≤ 10 as
  structural **only** when it is not attached to a metric name or unit.

Numbers echoed from the **question** count as supported: if the user asked
"what proportion reaches 80% of revenue?", the narration may say "80%".

**Worked example A — violation.**
Trusted metrics: `{households: 2500}`.
Narration: "There are 2,500 households across 47 regions."
→ `UNSUPPORTED_NUMBER`. `47` appears nowhere and is not structural; it is
attached to a unit ("regions") and asserted as fact.

**Worked example B — not a violation.**
Trusted metrics: `{revenue: 1234567.891}`.
Narration: "Revenue reached $1,234,567.89."
→ `PASS`. Rounding plus separators plus currency symbol; the value is the same.

---

### 2. `NUMERICALLY_WRONG`

The narration **names a metric that was supplied** and attaches a materially
different value to it. This is distinct from category 1: the number may exist
elsewhere in the metrics, but it is bound to the wrong quantity.

"Materially different" means differing beyond legitimate re-rendering above. A
difference in the third decimal place of a rounded figure is not material; a
figure that is the correct arithmetic on the wrong denominator is.

**Worked example A — violation.**
Trusted metrics: `{from_value: 800, to_value: 1000, percent_change: 25.0}`.
Narration: "Revenue grew 20% from 800 to 1,000."
→ `NUMERICALLY_WRONG`. `percent_change` was supplied as `25.0`; the narration
binds `20` to it (200/1000 — the new value as denominator).

**Worked example B — violation.**
Trusted metrics: `{revenue: 4100000, department: "GROCERY"}`.
Narration: "GROCERY generated 4,100,000 in profit."
→ Judge under this category only if a *profit* figure was supplied and differs.
Here none was, so this is **category 3** (asserting a concept the metrics do not
carry). Category 2 requires the metric to have been supplied.

---

### 3. `SEMANTICALLY_WRONG`

Every number is supported, but a **relationship, direction, ranking or concept**
claim contradicts the trusted metrics or is not supported by them.

Three sub-kinds, all labelled `SEMANTICALLY_WRONG`, distinguished in the `note`:

- **direction** — states a rise where the metrics fall, a fall where they rise,
  or any change where the values are identical.
- **ranking** — attaches a superlative ("highest", "largest", "peak",
  "strongest", "best", "leading", "most") to a row that is not the extreme one.
- **concept** — asserts something the metrics cannot carry: profit, margin,
  ROI, cost, forecast or prediction of a future value, annualisation, a causal
  explanation ("because", "driven by", "caused by"), or a claim of proof/no
  effect from a p-value.

**Negation and disclaimer are not assertion.** A narration that *declines* is
doing the right thing and passes:

- "it is **not possible to determine** profit margins from revenue alone" → PASS
- "the data **does not contain** a forecast for future weeks" → PASS
- "campaign growth **cannot be** evaluated from two weeks" → PASS

The test is whether the sentence containing the term **asserts** it. If the same
sentence contains a negation or an inability marker (not, cannot, without,
lacks, unable, no, insufficient, impossible, unavailable), it is a disclaimer.

**Worked example A — violation.**
Trusted metrics: `{week_88: 40000, week_89: 30000, direction: "decrease"}`.
Narration: "Revenue rose from 40,000.00 to 30,000.00."
→ `SEMANTICALLY_WRONG` (direction). Numbers correct, relationship inverted.

**Worked example B — violation.**
Trusted metrics: `{SOFT DRINKS: 250000, BEEF: 310000, CHEESE: 180000}`.
Narration: "SOFT DRINKS is the largest commodity at 250,000.00."
→ `SEMANTICALLY_WRONG` (ranking). BEEF is larger; row order is not rank.

**Worked example C — not a violation.**
Trusted metrics: `{revenue: 4100000}`.
Narration: "Revenue was 4,100,000.00. Profitability cannot be assessed because
the dataset lacks cost data."
→ `PASS`. "Profitability" appears, inside a disclaimer.

---

### 4. `VALID_REFUSAL`

The metrics make some requested quantity **undefined or unavailable**, and the
narration correctly declines to state it, while remaining accurate about what it
does report.

Distinguished from `PASS` only for reporting; both are acceptable outcomes and
both count as "not a failure" in any pass/fail collapse.

**Worked example A.**
Question: "By what percentage did revenue grow?"
Trusted metrics: `{from_value: 0, to_value: 500, percent_change: null,
percent_change_unavailable: "undefined from a zero baseline"}`.
Narration: "Revenue rose by 500.00. A percentage change from a zero baseline is
undefined."
→ `VALID_REFUSAL`.

**Worked example B.**
Question: "What share of revenue does each department hold?"
Trusted metrics: `{A: 0, B: 0}` (total zero).
Narration: "Both departments recorded zero revenue, so shares cannot be
computed."
→ `VALID_REFUSAL`.

---

### 5. `PASS`

Every numeric literal is supported, every direction and ranking claim matches,
and no unsupported concept is asserted. The default when no rule above fires.

**Worked example A.**
Trusted metrics: `{week_8: 91000, week_9: 64000, week_7: 70000}`.
Narration: "Week 8 achieved the highest revenue at 91,000.00, while week 9 was
the lowest at 64,000.00."
→ `PASS`.

**Worked example B.**
Trusted metrics: `{stores: 582, products: 92353}`.
Narration: "The dataset covers 582 stores and 92,353 products."
→ `PASS`. Two counts reported separately; no total invented.

---

## Adjudicator instructions

1. Read the question and the trusted metrics **first**. Form a view of what is
   true before reading the narration.
2. Read the narration once for meaning, once for numbers.
3. Apply the categories in order 1 → 2 → 3 → 4 → 5. Stop at the first match.
4. Put the specific offending value or phrase in `note`. A label without a note
   is not usable evidence.
5. If genuinely undecidable on the information shown, label `UNSURE` and say
   why. `UNSURE` is a legitimate outcome and is reported separately — guessing
   to fill a cell destroys the measurement this exercise exists to produce.

## What a failure of this rubric would look like

If the rubric is wrong, the symptom is systematic disagreement between the human
pass and the independent judge on a specific category — most likely category 3,
where "asserts" versus "discloses" is a judgement call that a marker list only
approximates. That disagreement is a finding about the rubric, not about the
model, and should be reported as such.
