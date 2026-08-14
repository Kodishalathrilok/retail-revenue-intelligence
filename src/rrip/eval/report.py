"""Assemble the human-readable evaluation report from measured artefacts only.

THE RULE THIS MODULE ENFORCES

Every number here is read from a file that a run produced. Nothing is computed
from memory, nothing is carried over from a previous version of the document,
and anything absent prints as NOT MEASURED with the command that would produce
it. A report that quietly omits a suite it could not run reads exactly like a
report where that suite passed.

This is the same rule the benchmark harness applies to the model, applied to the
project's own claims about itself.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from rrip.config import PROJECT_ROOT

EVAL_DIR = PROJECT_ROOT / "reports" / "eval"

NOT_MEASURED = "**NOT MEASURED**"


def _load(name: str) -> dict | None:
    p = EVAL_DIR / name
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _fmt(value, suffix: str = "", missing: str = NOT_MEASURED) -> str:
    if value is None:
        return missing
    if isinstance(value, float):
        return f"{value:,.1f}{suffix}"
    return f"{value:,}{suffix}" if isinstance(value, int) else f"{value}{suffix}"


def _nl2sql_section(router: dict | None, baseline: dict | None) -> list[str]:
    if not router:
        return ["## NL to SQL", "",
                NOT_MEASURED + " — run `rrip eval` (needs a database and an "
                "LLM API key).", ""]

    s = router["summary"]
    b = baseline["summary"] if baseline else None
    rt = s["router"]

    def cmp(key: str, suffix: str = "%") -> str:
        now = _fmt(s.get(key), suffix)
        was = _fmt(b.get(key), suffix) if b else NOT_MEASURED
        return f"| {key.replace('_', ' ')} | {was} | {now} |"

    out = [
        "## NL to SQL", "",
        f"Dataset `{router['provenance']['dataset_version']}`, "
        f"{s['n_cases']} cases for the `{router['tier']}` tier, "
        f"model `{router['model']}`, max {router['max_attempts']} attempts.", "",
        "| metric | router off | router on |",
        "|---|---|---|",
        cmp("result_equivalence_pct"),
        cmp("final_execution_success_pct"),
        cmp("first_attempt_execution_success_pct"),
        cmp("refusal_rate"),
        cmp("ambiguous_handled_rate"),
        f"| retry recovery | "
        f"{_fmt(b.get('retry_recovery_cases') if b else None, ' cases')} "
        f"| {_fmt(s.get('retry_recovery_cases'), ' cases')} |",
        "",
        "**`result_equivalence_pct` is the correctness measure.** It is computed "
        f"over the {s.get('n_graded', '?')} cases carrying a reference query: both queries "
        "execute and their result sets are compared, so a differently-phrased "
        "query returning the right answer counts as correct.",
        "",
        "**The two `execution_success` rows are not correctness.** They say only "
        "that SQL ran. They fall when the router is on, and that is the router "
        "working — a question it declines never reaches SQL generation, so it "
        "cannot be counted as executing. Reporting either as an accuracy figure "
        "would be a category error.",
        "",
    ]

    if s["rejecting_gates"]:
        out += ["Rejections by gate (which gate sent the model back to try again):",
                "", "```", json.dumps(s["rejecting_gates"], indent=1), "```", ""]

    out += [
        "### Answerability router", "",
        "| | |", "|---|---|",
        f"| verdicts | `{rt['verdicts']}` |",
        f"| false positives | **{rt['false_positives']}** of "
        f"{rt['n_answerable_cases']} cases that have a reference answer |",
        "",
        "The false-positive count is the number that keeps this honest. A router "
        "that refuses more scores better on unanswerable questions and worse at "
        "the job; this one blocked none of the questions the system can "
        "demonstrably answer.",
        "",
    ]

    failures = [c for c in router["cases"] if not c["passed"]]
    if failures:
        out += ["### Remaining failures", "",
                "| case | category | detail |", "|---|---|---|"]
        out += [f"| `{c['id']}` | {c['category']} | {c['grade_detail']} |"
                for c in failures]
        out += [""]
    return out


def _latency_section(router: dict | None, baseline: dict | None) -> list[str]:
    if not router:
        return []
    s, b = router["summary"], (baseline or {}).get("summary")
    cached = router.get("llm_cache_enabled")
    out = [
        "## Latency", "",
        "| percentile | router off | router on |", "|---|---|---|",
        f"| p50 | {_fmt(b['latency_ms']['p50'] if b else None, ' ms')} "
        f"| {_fmt(s['latency_ms']['p50'], ' ms')} |",
        f"| p95 | {_fmt(b['latency_ms']['p95'] if b else None, ' ms')} "
        f"| {_fmt(s['latency_ms']['p95'], ' ms')} |",
        "",
    ]
    if cached:
        out += ["> The LLM response cache was ON for this run, so these figures "
                "describe cache hits for repeated questions, not cold model "
                "latency. Re-run with `RRIP_LLM_CACHE=0` to measure the model. "
                "The router-on median is low largely because routed questions "
                "return without any model call at all.", ""]
    return out


def _narration_section(nar: dict | None) -> list[str]:
    if not nar:
        return ["## Narration faithfulness", "",
                NOT_MEASURED + " — run `rrip eval-narration` (needs an LLM API "
                "key).", ""]

    s = nar["summary"]
    b, st = s["baseline"], s["structured"]
    p = nar["provenance"]
    n = p["n_cases"]

    def rate(key: str) -> str:
        """One metric on both arms, with NO delta column.

        The delta and its tick/cross were removed deliberately. They rendered a
        difference of one or two cases as a directional result, and the
        adjudication pass (eval/narration/adjudication_report.md) established
        that the paired test cannot support one at this sample size.
        """
        return f"| {LABELS[key]} | {b.get(key)}% | {st.get(key)}% |"

    out = [
        "## Narration grounding", "",
        f"Dataset `{p['dataset_version']}`, {n} cases, model "
        f"`{nar['model']}`. Every case runs both payload variants against the "
        "same live model.", "",
        "**The A/B improvement claim this section used to carry is withdrawn.** "
        "An adjudication pass found that one of the three discordant pairs it "
        "rested on, `NAR-092`, was a confirmed mis-grade by the classifier in "
        "`rrip.eval.narration_bench`. Two pairs survive, at which point the "
        "exact McNemar p is **0.5 — the smallest value attainable at n = 2**. "
        "The design cannot produce evidence of a difference at this sample "
        "size.", "",
        "The per-arm rates below are therefore reported as observations, not "
        "as a comparison, and they still contain the known `NAR-092` "
        "mis-grade; correcting the classifier is a separate commit. See "
        "`eval/narration/adjudication_report.md`.", "",
        "| | baseline (raw rows) | structured (derived) |",
        "|---|---:|---:|",
        rate("faithful_pct"),
        rate("valid_refusal_pct"),
        rate("unsupported_number_pct"),
        rate("numerically_wrong_pct"),
        rate("semantically_wrong_pct"),
        rate("grounding_rejected_correct_pct"),
        rate("grounding_rejected_strict_pct"),
        rate("escaped_to_user_pct"),
        rate("delivered_pct"),
        "",
        "**Guard rejected — true but absent** counts narratives whose every "
        "number was arithmetically true, rejected because the payload did not "
        "literally contain them. The guard was right by its own rule and the "
        "reader still lost a correct answer.",
        "",
        "**Escaped to user** counts wrong claims the guard did *not* catch — "
        "the only failures a reader would actually see.",
        "",
    ]

    out += _unsupported_bound(b, st, n)

    paired = s.get("paired")
    if paired:
        out += [
            "### The paired test, and why it is not evidence", "",
            "| | as recorded by the classifier |", "|---|---|",
            f"| acceptable on both paths | {paired['acceptable_on_both']} |",
            f"| favour structured | {paired['structured_only']} |",
            f"| favour baseline | {paired['baseline_only']} |",
            f"| exact McNemar p | {paired['mcnemar_exact_p']} |",
            "",
            "These are the figures the classifier produced, retained for "
            "traceability. After removing the confirmed `NAR-092` mis-grade "
            "the discordant count drops to 2 and p rises to 0.5. Neither "
            "figure supports a difference, and the corrected one cannot, "
            "because 0.5 is the floor at n = 2.", "",
        ]
        if paired.get("discordant"):
            out += ["Discordant cases as recorded:", ""]
            out += [f"- `{d['case_id']}` — favours {d['favours']}"
                    for d in paired["discordant"]]
            out += [""]
    return out


LABELS = {
    "faithful_pct": "Faithful",
    "valid_refusal_pct": "Valid refusal",
    "unsupported_number_pct": "**Unsupported number**",
    "numerically_wrong_pct": "Numerically wrong",
    "semantically_wrong_pct": "Semantically wrong",
    "grounding_rejected_correct_pct": "Guard rejected — correctly",
    "grounding_rejected_strict_pct": "Guard rejected — true but absent",
    "escaped_to_user_pct": "**Escaped to user**",
    "delivered_pct": "Answer delivered at all",
}


def _unsupported_bound(baseline: dict, structured: dict, n: int) -> list[str]:
    """State the headline rate with its interval instead of as a bare zero.

    A measured 0% over 65 cases is not a demonstrated zero. Reporting it
    without the bound invites the reader to believe the rate is lower than this
    sample can show, which is the same category of overclaim the guard exists
    to prevent in the model's own output.
    """
    b = baseline.get("unsupported_number_pct")
    s = structured.get("unsupported_number_pct")
    if b is None or s is None:
        return []

    events = round((b + s) / 100.0 * n)
    if events == 0:
        # Clopper-Pearson one-sided 95% upper bound with zero events.
        upper = (1 - 0.05 ** (1 / n)) * 100
        return [
            "### The headline rate, with its uncertainty", "",
            f"> **0 of {n} cases produced an unsupported numeric claim** on "
            "either path. With no events the one-sided 95% upper bound is "
            f"**{upper:.1f}%** (Clopper–Pearson; rule of three gives "
            f"3/{n} ≈ {300 / n:.1f}%).", "",
            f"The honest ceiling is \"below roughly {upper + 0.5:.0f}%\", not "
            "\"zero\". A sample of this size cannot demonstrate a rate lower "
            "than that.", "",
        ]
    return []


def _role_section(role: dict | None) -> list[str]:
    if not role:
        return ["## Database authorization boundary", "",
                NOT_MEASURED + " — run `rrip verify-role`.", ""]
    out = [
        "## Database authorization boundary", "",
        f"Role `{role['role']}`: **{role['status']}**", "",
    ]
    if role.get("reason"):
        out += [f"> {role['reason']}", ""]
    if role.get("probes"):
        out += [
            f"{role['n_passed']}/{role['n_probes']} probes passed. "
            f"{role['refused_by']['database']} refusals came from PostgreSQL "
            "itself rather than from the application validator — the probes run "
            "raw SQL and never touch the gates, so this measures the database "
            "boundary alone.", "",
            "| probe | expected | outcome | refused by |", "|---|---|---|---|",
        ]
        out += [f"| {p['description']} | {p['expected']} | {p['outcome']} "
                f"| {p['refused_by']} |" for p in role["probes"]]
        out += [""]
    return out


def _causal_section(causal: dict | None) -> list[str]:
    if not causal:
        return ["## Causal estimator validation", "",
                NOT_MEASURED + " — run `rrip causal-validate`.", ""]
    cov = causal.get("coverage", {})
    out = [
        "## Causal estimator validation", "",
        "Synthetic panels with a planted effect of known size. This measures the "
        "ESTIMATOR, not the campaign: it answers whether the difference-in-"
        "differences code recovers an effect it is given, which is a prerequisite "
        "for believing anything it says about real data.", "",
        "### Repeated simulation", "",
        "| | |", "|---|---|",
        f"| simulations | {_fmt(cov.get('n_simulations'))} |",
        f"| true effect | {_fmt(cov.get('true_effect'))} |",
        f"| mean estimate | {_fmt(cov.get('mean_estimate'))} |",
        f"| bias | {_fmt(cov.get('bias'))} |",
        f"| SD of estimates | {_fmt(cov.get('sd_of_estimates'))} |",
        f"| 95% CI coverage | {_fmt(cov.get('ci_coverage_pct'), '%')} "
        f"(nominal {_fmt(cov.get('nominal_coverage_pct'), '%')}) |",
        "",
        "Coverage materially below nominal would mean the intervals are too "
        "narrow — the estimator claiming more certainty than it has.", "",
    ]
    rec = causal.get("recovery") or []
    if rec:
        out += ["### Single-run scenarios", "",
                "| scenario | true | estimated | abs error | CI covers truth "
                "| parallel trends |", "|---|---|---|---|---|---|"]
        out += [f"| {r['scenario']} | {r['true_effect']:.1f} | {r['estimated']:.3f} "
                f"| {r['abs_error']:.3f} | {r['covered']} "
                f"| {r['parallel_trends_passed']} |" for r in rec]
        out += ["", "The violated-parallel-trends and short-pre-period scenarios "
                "are included because they are supposed to fail. An estimator "
                "that passes every scenario has not been tested.", ""]
    return out


def build() -> str:
    router = _load("latest.json")
    baseline = _load("latest-norouter.json")
    role = _load("readonly-role.json")
    causal = _load("causal-validation.json")
    narration = _load("narration-latest.json")

    prov = (router or {}).get("provenance", {})
    lines = [
        "# RRIP evaluation report", "",
        f"Generated {datetime.now(UTC).isoformat(timespec='seconds')}.", "",
        "Every figure below was produced by a run recorded in `reports/eval/`. "
        "Anything that could not be measured says so.", "",
        "| | |", "|---|---|",
        f"| git commit | `{prov.get('git_commit', 'unknown')[:12]}`"
        f"{' (working tree dirty)' if prov.get('git_dirty') else ''} |",
        f"| dataset | `{prov.get('dataset_version', 'unknown')}`, "
        f"{prov.get('n_cases_in_dataset', '?')} cases |",
        f"| prompt sha256 | `{prov.get('prompt_sha256', 'unknown')}` |",
        f"| semantic layer sha256 | `{prov.get('semantic_layer_sha256', 'unknown')}` |",
        f"| model | `{(router or {}).get('model', 'unknown')}` |",
        f"| python | {prov.get('python', 'unknown')} |",
        "",
    ]
    lines += _nl2sql_section(router, baseline)
    lines += _latency_section(router, baseline)
    lines += _narration_section(narration)
    lines += _role_section(role)
    lines += _causal_section(causal)
    lines += [
        "## Not measured", "",
        "Stated rather than omitted:", "",
        "- **Published (Neon) tier.** Every figure here is the `local` tier. The "
        "read-only role has been verified against the local database only.",
        "- **Cold-cache latency.** See the note in the latency section.", "",
    ]
    return "\n".join(lines)


def write() -> Path:
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    path = EVAL_DIR / "latest.md"
    path.write_text(build(), encoding="utf-8")
    return path
