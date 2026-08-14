"""Agreement analysis: original classifier vs independent judge vs human.

FALSE PASS AND FALSE FAIL ARE REPORTED SEPARATELY AND NEVER SUMMED

An accuracy figure hides the asymmetry that motivated this audit. A false FAIL
costs a good narration and shows up immediately as an investigable failure -- the
first benchmark run found five of them, because failures are what people look at.
A false PASS is invisible: it inflates the headline faithfulness rate and nobody
goes looking. They are different defects with different consequences and they are
counted apart.

NEITHER REFERENCE IS GROUND TRUTH

The judge is a second implementation of the same rubric, written by the same
author. Disagreement means the two implementations differ; it does not establish
which is right. Everything below is therefore phrased as CANDIDATE defects.
Only the human column carries independent authority, and only for the 50 rows it
covers.
"""

from __future__ import annotations

import csv
import json
import sys
from collections import Counter, defaultdict
from math import comb
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPORT = HERE / "adjudication_report.md"
ORIGINAL_RUN = HERE.parents[1] / "reports" / "eval" / "narration-latest.json"

# Labels that mean "this output is acceptable" in each vocabulary.
ORIGINAL_OK = {"FAITHFUL", "VALID_REFUSAL"}
JUDGE_OK = {"PASS", "VALID_REFUSAL"}
HUMAN_OK = {"PASS", "VALID_REFUSAL"}

# The two vocabularies name the same verdict differently. Without this mapping
# every agreeing row scores as a disagreement and kappa collapses to ~0 -- which
# is a defect in the ANALYSIS, not a finding about the classifier. Caught on the
# first run of this script, when full-space kappa came out at 0.029 while the
# collapsed pass/fail kappa was 0.23; a genuine disagreement cannot be larger in
# the coarse view than in the fine one.
VOCAB = {"FAITHFUL": "PASS"}

# Guard-level outcomes have no counterpart in the judge's vocabulary: the judge
# reads the narration text and cannot observe whether the guard delivered it.
# Rows with these verdicts are held out of kappa and reported on their own,
# rather than being forced onto an axis that does not exist.
GUARD_ONLY = {"GROUNDING_REJECTED_CORRECT", "GROUNDING_REJECTED_STRICT"}


def normalise(verdict: str) -> str:
    return VOCAB.get(verdict, verdict)


# Manual adjudication of every original-vs-judge disagreement.
#
# THIS IS NOT THE HUMAN PASS. It is the report author resolving which of two
# implementations is right on each disputed row, by reading the narration and
# the metrics. It is recorded here rather than in prose so the reasoning travels
# with the numbers, and it is labelled separately everywhere it appears because
# its author also wrote the rubric and the judge.
#
# verdict: "original_correct" | "judge_correct" | "unresolved"
ADJUDICATION: dict[tuple[str, str], tuple[str, str]] = {
    ("NAR-132", "structured"): (
        "original_correct",
        "Judge defect. Its RISE list is matched as substrings without word "
        "boundaries, and 'rise' is contained in 'comprises'. Compounding it, "
        "the judge infers direction from row order, but these rows are two "
        "SEGMENTS, not a time series -- there is no direction to describe."),
    ("NAR-132", "baseline"): (
        "original_correct",
        "Same judge defect: 'rise' inside 'comprises', plus direction inferred "
        "from a non-temporal ordering."),
    ("NAR-030", "structured"): (
        "unresolved",
        "Narration says value is 'driven by this customer tier'. The rubric "
        "lists 'driven by' as a causal marker, so the judge applied it "
        "correctly; but the phrase is attributing a SHARE, not claiming a "
        "mechanism. The rubric's marker list is too blunt here. This is a "
        "defect in the RUBRIC, which predicted exactly this failure mode for "
        "category 3."),
    ("NAR-180", "baseline"): (
        "unresolved",
        "Same as NAR-030: 'sales driven by a small elite segment' is a "
        "concentration statement phrased causally. Rubric bluntness, not a "
        "confirmed classifier fault."),
    ("NAR-031", "baseline"): (
        "not_a_disagreement",
        "The judge reads narration text and found it sound; the original "
        "records that the GUARD rejected it before delivery. Both are right "
        "about different things. This row is why guard verdicts are held out "
        "of kappa."),
    ("NAR-092", "baseline"): (
        "judge_correct",
        "CONFIRMED DEFECT IN THE ORIGINAL CLASSIFIER. Narration: 'The dataset "
        "contains no positive values to calculate a meaningful growth rate.' "
        "_is_negated() returns False for it -- NEGATION_MARKERS contains "
        "'no cost' but no bare 'no ' -- so the sentence stays live, 'growth' "
        "matches INCREASE_WORDS, and a correct refusal is scored "
        "SEMANTICALLY_WRONG. The narration is accurate throughout."),
}


def load_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def kappa(pairs: list[tuple[str, str]]) -> float | None:
    """Cohen's kappa on paired categorical labels."""
    if not pairs:
        return None
    n = len(pairs)
    observed = sum(1 for a, b in pairs if a == b) / n
    a_counts = Counter(a for a, _ in pairs)
    b_counts = Counter(b for _, b in pairs)
    expected = sum((a_counts[k] / n) * (b_counts[k] / n)
                   for k in set(a_counts) | set(b_counts))
    if expected == 1.0:
        return None            # degenerate: one label everywhere
    return round((observed - expected) / (1 - expected), 4)


def mcnemar_exact(struct_only: int, base_only: int) -> float | None:
    n = struct_only + base_only
    if not n:
        return None
    k = min(struct_only, base_only)
    return round(min(sum(comb(n, i) for i in range(k + 1)) / 2 ** n * 2, 1.0), 4)


def matrix_md(pairs: list[tuple[str, str]], row_name: str, col_name: str) -> list[str]:
    rows = sorted({a for a, _ in pairs})
    cols = sorted({b for _, b in pairs})
    grid: dict[tuple[str, str], int] = defaultdict(int)
    for a, b in pairs:
        grid[(a, b)] += 1
    out = [f"| {row_name} \\ {col_name} | " + " | ".join(cols) + " | total |",
           "|---" * (len(cols) + 2) + "|"]
    for r in rows:
        cells = [str(grid[(r, c)]) if grid[(r, c)] else "·" for c in cols]
        out.append(f"| **{r}** | " + " | ".join(cells) + f" | {sum(grid[(r, c)] for c in cols)} |")
    out.append("| **total** | "
               + " | ".join(str(sum(grid[(r, c)] for r in rows)) for c in cols)
               + f" | {len(pairs)} |")
    return out


def main() -> int:
    key = {r["row_id"]: r for r in load_csv(HERE / "adjudication_key.csv")}
    judge = {r["row_id"]: r for r in load_csv(HERE / "adjudication_judge.csv")}
    human = {r["row_id"]: r for r in load_csv(HERE / "adjudication_worksheet_human.csv")
             if r.get("label", "").strip()}
    human_total = len(load_csv(HERE / "adjudication_worksheet_human.csv"))

    if not ORIGINAL_RUN.exists():
        print(f"missing {ORIGINAL_RUN}", file=sys.stderr)
        return 1
    run = json.loads(ORIGINAL_RUN.read_text(encoding="utf-8"))
    original = {(o["case_id"], o["path"]): o for o in run["outcomes"]}

    # --- original vs judge, all rows ---------------------------------------
    oj_pairs: list[tuple[str, str]] = []
    guard_rows: list[dict] = []
    cand_false_pass: list[dict] = []
    cand_false_fail: list[dict] = []
    for row_id, k in key.items():
        orig = original.get((k["case_id"], k["arm"]))
        j = judge.get(row_id)
        if not orig or not j:
            continue
        if orig["verdict"] in GUARD_ONLY:
            guard_rows.append({"case_id": k["case_id"], "arm": k["arm"],
                               "original": orig["verdict"], "judge": j["label"]})
        else:
            oj_pairs.append((normalise(orig["verdict"]), j["label"]))
        o_ok = orig["verdict"] in ORIGINAL_OK
        j_ok = j["label"] in JUDGE_OK
        entry = {"case_id": k["case_id"], "arm": k["arm"], "row_id": row_id,
                 "original": orig["verdict"], "judge": j["label"],
                 "judge_category": j["category"], "judge_note": j["note"]}
        if o_ok and not j_ok:
            cand_false_pass.append(entry)
        elif j_ok and not o_ok:
            cand_false_fail.append(entry)

    # --- collapsed pass/fail ------------------------------------------------
    collapsed = [("ACCEPTABLE" if a in (ORIGINAL_OK | {"PASS"}) else "FAILURE",
                  "ACCEPTABLE" if b in JUDGE_OK else "FAILURE")
                 for a, b in oj_pairs]

    lines = [
        "# Narration adjudication report", "",
        "Symmetric audit of the narration benchmark's pass labels. The first "
        "investigation hunted bugs only among the seven flagged FAILURES and "
        "found five defects in the measuring apparatus. This asks the question "
        "that was never asked: how many of the 123 PASSES are wrong?", "",
        "Rubric pre-registered in "
        "[`adjudication_rubric.md`](adjudication_rubric.md) and committed alone "
        "before any label existed. Shuffle seed **20260814**. No model API calls "
        "— all 130 narrations recovered from the on-disk response cache.", "",
        "## Corpus", "",
        "| | |", "|---|---|",
        f"| narrations adjudicated | {len(key)} (65 cases × 2 arms) |",
        f"| recovered from cache | {len(key)}/130 |",
        f"| independent judge labels | {len(judge)} |",
        f"| human labels filled in | **{len(human)} / {human_total}** |",
        "",
    ]

    if not human:
        lines += [
            "> **The human pass is not yet filled in.** "
            "`adjudication_worksheet_human.csv` has 50 blank `label` rows "
            "awaiting hand adjudication. Every section below that depends on "
            "human labels reports **NOT MEASURED** rather than substituting the "
            "judge for a human — the judge shares this rubric's author and "
            "cannot stand in for an independent reader.", "",
        ]

    # --- blinding verification ---------------------------------------------
    ws_rows = load_csv(HERE / "adjudication_worksheet.csv")
    ws_cols = list(ws_rows[0].keys()) if ws_rows else []
    leak_tokens = ("NAR-", "structured", "FAITHFUL", "SEMANTICALLY",
                   "VALID_REFUSAL", "GROUNDING_REJECTED")
    leaks = {t: sum(r[c].count(t) for r in ws_rows for c in ws_cols)
             for t in leak_tokens}
    arms_saying_baseline = {key[r["row_id"]]["arm"] for r in ws_rows
                            if "baseline" in r["narration"] and r["row_id"] in key}
    lines += [
        "## Blinding", "",
        f"Worksheet columns: `{'`, `'.join(ws_cols)}`. No arm, no original "
        "label, no case id.", "",
        "| token | occurrences in worksheet |", "|---|---:|",
    ]
    lines += [f"| `{t}` | {n} |" for t, n in leaks.items()]
    lines += [
        "",
        "The word *baseline* does occur, but as ordinary English inside model "
        f"narrations (\"provides the baseline performance…\") and in a `period` "
        f"value. It appears in rows from {len(arms_saying_baseline)} of the two "
        "arms, so it does not identify one.", "",
        "**The one real leak**, stated in `build_worksheet.py` and repeated "
        "here: `trusted_metrics` *is* the payload, and the structured arm's "
        "payload carries `totals` and `change_over_period` keys the baseline "
        "arm lacks. A reader who knows the system can infer the arm from shape. "
        "Withholding those keys would hide the facts against which faithfulness "
        "is judged, so the leak is accepted and disclosed rather than closed. "
        "The blinding protects against **label** leakage, not arm leakage.", "",
    ]

    lines += ["## Original classifier vs independent judge (n = "
              f"{len(oj_pairs)})", ""]
    lines += matrix_md(oj_pairs, "original", "judge")
    lines += [
        "",
        f"Cohen's kappa (full label space): **{kappa(oj_pairs)}**", "",
        "Collapsed to acceptable / failure:", "",
    ]
    lines += matrix_md(collapsed, "original", "judge")
    lines += ["", f"Cohen's kappa (collapsed): **{kappa(collapsed)}**", ""]
    lines += [
        f"`FAITHFUL` is normalised to `PASS` — the two vocabularies name the "
        f"same verdict differently, and without that mapping every agreeing row "
        f"scores as a disagreement. {len(guard_rows)} row(s) with guard-level "
        "verdicts are held out of kappa entirely: the judge reads narration "
        "text and cannot observe whether the guard delivered it, so there is no "
        "axis to compare on.", "",
    ]
    if guard_rows:
        lines += ["| case | arm | original | judge saw the text as |",
                  "|---|---|---|---|"]
        lines += [f"| `{g['case_id']}` | {g['arm']} | {g['original']} | "
                  f"{g['judge']} |" for g in guard_rows]
        lines += [""]

    lines += [
        "## False passes and false fails", "",
        "Counted separately and never summed. Neither reference is ground "
        "truth, so these are **candidates** — rows where the two "
        "implementations disagree.", "",
        "| | count |", "|---|---:|",
        f"| candidate FALSE PASS (original accepted, judge failed) | "
        f"**{len(cand_false_pass)}** |",
        f"| candidate FALSE FAIL (original failed, judge accepted) | "
        f"**{len(cand_false_fail)}** |",
        "",
    ]

    if cand_false_pass:
        lines += ["### Candidate false passes", "",
                  "| case | arm | original | judge | why |",
                  "|---|---|---|---|---|"]
        lines += [f"| `{e['case_id']}` | {e['arm']} | {e['original']} | "
                  f"{e['judge']} ({e['judge_category']}) | {e['judge_note']} |"
                  for e in sorted(cand_false_pass, key=lambda x: x["case_id"])]
        lines += [""]
    else:
        lines += ["No candidate false passes: the judge accepted every output "
                  "the original accepted.", ""]

    if cand_false_fail:
        lines += ["### Candidate false fails", "",
                  "| case | arm | original | judge | why |",
                  "|---|---|---|---|---|"]
        lines += [f"| `{e['case_id']}` | {e['arm']} | {e['original']} | "
                  f"{e['judge']} | {e['judge_note'] or '—'} |"
                  for e in sorted(cand_false_fail, key=lambda x: x["case_id"])]
        lines += [""]

    # --- human sections -----------------------------------------------------
    lines += ["## Original classifier vs human", ""]
    if not human:
        lines += ["**NOT MEASURED** — 0 of 50 human labels filled in. Complete "
                  "`adjudication_worksheet_human.csv` and re-run "
                  "`analyse_agreement.py`.", ""]
    else:
        hp = []
        h_false_pass, h_false_fail = [], []
        for row_id, h in human.items():
            k = key.get(row_id)
            orig = original.get((k["case_id"], k["arm"])) if k else None
            if not orig or h["label"].strip().upper() == "UNSURE":
                continue
            hl = h["label"].strip().upper()
            hp.append((orig["verdict"], hl))
            o_ok, h_ok = orig["verdict"] in ORIGINAL_OK, hl in HUMAN_OK
            rec = {"case_id": k["case_id"], "arm": k["arm"],
                   "original": orig["verdict"], "human": hl,
                   "note": h.get("note", "")}
            if o_ok and not h_ok:
                h_false_pass.append(rec)
            elif h_ok and not o_ok:
                h_false_fail.append(rec)
        unsure = sum(1 for h in human.values()
                     if h["label"].strip().upper() == "UNSURE")
        lines += matrix_md(hp, "original", "human") if hp else ["(no usable rows)"]
        lines += ["", f"Cohen's kappa: **{kappa(hp)}**",
                  f"  ·  UNSURE rows excluded: {unsure}", "",
                  "| | count |", "|---|---:|",
                  f"| FALSE PASS vs human | **{len(h_false_pass)}** |",
                  f"| FALSE FAIL vs human | **{len(h_false_fail)}** |", ""]
        for title, group in (("False passes vs human", h_false_pass),
                             ("False fails vs human", h_false_fail)):
            if group:
                lines += [f"### {title}", "", "| case | arm | original | human | note |",
                          "|---|---|---|---|---|"]
                lines += [f"| `{g['case_id']}` | {g['arm']} | {g['original']} | "
                          f"{g['human']} | {g['note']} |" for g in group]
                lines += [""]

    # --- recomputed faithfulness -------------------------------------------
    lines += ["## Faithfulness recomputed", ""]
    s = run["summary"]
    lines += [
        "Originally reported:", "",
        "| | baseline | structured |", "|---|---:|---:|",
        f"| faithful | {s['baseline']['faithful_pct']}% | "
        f"{s['structured']['faithful_pct']}% |",
        f"| paired McNemar p | colspan | {s['paired']['mcnemar_exact_p']} |",
        "",
    ]
    if not human:
        lines += ["Recomputed from human labels: **NOT MEASURED** (see above).",
                  "", "Recomputed from the independent judge:", ""]
        j_by_arm: dict[str, list[str]] = defaultdict(list)
        for row_id, k in key.items():
            if row_id in judge:
                j_by_arm[k["arm"]].append(judge[row_id]["label"])
        lines += ["| | baseline | structured |", "|---|---:|---:|"]
        for label, keep in (("acceptable (PASS or VALID_REFUSAL)", JUDGE_OK),):
            cells = []
            for arm in ("baseline", "structured"):
                vals = j_by_arm[arm]
                cells.append(f"{round(100 * sum(1 for v in vals if v in keep) / len(vals), 1)}%"
                             if vals else "—")
            lines += [f"| {label} | " + " | ".join(cells) + " |"]

        # paired test on judge labels
        by_case: dict[str, dict[str, str]] = defaultdict(dict)
        for row_id, k in key.items():
            if row_id in judge:
                by_case[k["case_id"]][k["arm"]] = judge[row_id]["label"]
        s_only = sum(1 for v in by_case.values()
                     if len(v) == 2 and v["structured"] in JUDGE_OK
                     and v["baseline"] not in JUDGE_OK)
        b_only = sum(1 for v in by_case.values()
                     if len(v) == 2 and v["baseline"] in JUDGE_OK
                     and v["structured"] not in JUDGE_OK)
        p = mcnemar_exact(s_only, b_only)
        lines += ["", f"Paired on judge labels: {s_only} favour structured, "
                      f"{b_only} favour baseline, exact McNemar "
                      f"**p = {p}**.", ""]

        # --- corrected for the one confirmed classifier defect --------------
        orig_disc = s["paired"]["discordant"]
        confirmed_bad = {cid for (cid, _arm), (v, _w) in ADJUDICATION.items()
                         if v == "judge_correct"}
        surviving = [d for d in orig_disc if d["case_id"] not in confirmed_bad]
        removed = [d for d in orig_disc if d["case_id"] in confirmed_bad]
        p_corr = mcnemar_exact(
            sum(1 for d in surviving if d["favours"] == "structured"),
            sum(1 for d in surviving if d["favours"] == "baseline"))
        lines += [
            "### Corrected paired test", "",
            "The reported A/B rested on three discordant pairs. One of them, "
            "`NAR-092`, is a **confirmed false fail** — the baseline narration "
            "was accurate and was graded wrong. Removing only that row, and "
            "changing nothing else:", "",
            "| | discordant pairs | exact McNemar p |", "|---|---:|---:|",
            f"| as reported | {len(orig_disc)} | "
            f"{s['paired']['mcnemar_exact_p']} |",
            f"| corrected | {len(surviving)} | **{p_corr}** |",
            "",
            f"Rows removed: {', '.join('`' + d['case_id'] + '`' for d in removed)}. "
            f"Surviving: {', '.join('`' + d['case_id'] + '`' for d in surviving)}.",
            "",
            "Every surviving discordant pair still favours the structured arm "
            "and none favours baseline, but that direction is not evidence. "
            f"**p = {p_corr} is the smallest value attainable with "
            f"{len(surviving)} discordant pairs**: even a perfect "
            f"{len(surviving)}-0 split cannot go below it. The design has no "
            "power to detect a difference at this sample size, so the A/B "
            "claim is withdrawn rather than described as thin.", "",
            "What survives is architectural and does not rest on this test: "
            "numeric metrics are computed deterministically by "
            "`rrip.ai.derive`, and the narration layer is evaluated against "
            "them. That is verifiable by reading the wiring.", "",
            "The per-arm faithfulness percentages are NOT restated here. "
            "Recomputing them would mean re-running the classifier with the "
            "defect fixed, and fixing it is out of scope for this task by "
            "instruction. The corrected figure is deferred to that commit.", "",
        ]

    # --- manual adjudication of the disagreements --------------------------
    disputed = cand_false_pass + cand_false_fail
    verdicts = Counter()
    lines += [
        "## Adjudicating the disagreements", "",
        "Each disputed row read by hand against the rubric. **This is not the "
        "human pass** — it is the report author deciding which implementation "
        "is right, and its author also wrote the rubric and the judge. Labelled "
        "separately for that reason.", "",
        "| case | arm | original | judge | who was right | why |",
        "|---|---|---|---|---|---|",
    ]
    for e in sorted(disputed, key=lambda x: x["case_id"]):
        verdict, why = ADJUDICATION.get(
            (e["case_id"], e["arm"]), ("unadjudicated", "—"))
        verdicts[verdict] += 1
        lines.append(f"| `{e['case_id']}` | {e['arm']} | {e['original']} | "
                     f"{e['judge']} | **{verdict}** | {why} |")
    lines += ["", "| outcome | count |", "|---|---:|"]
    for v, n in verdicts.most_common():
        lines.append(f"| {v} | {n} |")
    lines += [
        "",
        f"**Confirmed false passes by the original classifier: "
        f"{verdicts['judge_correct_false_pass']}.** The audit set out to find "
        "them and found none. What it found instead is one confirmed false "
        "FAIL, two judge defects, and two rows where the rubric is too blunt to "
        "decide.", "",
    ]

    lines += [
        "## Newly suspected defects", "",
        "Logged, **not fixed** — fixing is a separate commit by instruction.", "",
        "### In the original classifier (confirmed)", "",
        "- **`NAR-092` baseline — false FAIL.** "
        "`rrip.eval.narration_bench.NEGATION_MARKERS` contains `'no cost'` but "
        "no bare `'no '`, so *\"contains **no** positive values to calculate a "
        "meaningful growth rate\"* is not recognised as a disclaimer. The "
        "sentence stays live, `'growth'` matches `INCREASE_WORDS`, and a "
        "correct refusal is graded `SEMANTICALLY_WRONG`. "
        "**This row is one of the three discordant pairs behind the reported "
        "A/B result** — see the recomputation below.", "",
        "### In the independent judge (confirmed, this audit's own tool)", "",
        "- Direction words are matched as **substrings without word "
        "boundaries**: `'rise'` matches inside `'comprises'`. Hit `NAR-132` on "
        "both arms.",
        "- `infer_direction` treats **row order as a time axis**. For "
        "`NAR-132` the rows are two customer segments; there is no direction to "
        "infer and it invented one.", "",
        "### In the rubric (unresolved)", "",
        "- Category 3's causal marker list contains `'driven by'`, which "
        "ordinary analytical prose uses for **attribution** (\"45.6% of revenue "
        "driven by Champions\") as well as for causation. `NAR-030` and "
        "`NAR-180` turn on this and are recorded as unresolved rather than "
        "decided. The rubric predicted this failure mode for category 3.", "",
    ]

    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {REPORT}")
    print(f"  candidate false passes: {len(cand_false_pass)}")
    print(f"  candidate false fails : {len(cand_false_fail)}")
    print(f"  kappa (full)          : {kappa(oj_pairs)}")
    print(f"  kappa (collapsed)     : {kappa(collapsed)}")
    print(f"  human labels          : {len(human)}/{human_total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
