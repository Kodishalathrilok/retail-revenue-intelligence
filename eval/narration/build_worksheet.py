"""Build the blind adjudication worksheet from CACHED narration outputs.

NO API CALLS. Every narration is recovered from the on-disk LLM response cache
by rebuilding the exact prompt that produced it and looking up its cache key.

WHY NOT READ reports/eval/narration-latest.json

That report stores `narrative_text` truncated to 600 characters, and it stores
prose already flattened by the classifier under audit -- including the
field-joining behaviour that was itself one of the defects found. An audit that
reads the classifier's own flattening inherits its bugs. So the raw cached
response is parsed here and flattened locally, with one field per line, which
cannot merge two claims into one sentence.

BLINDING, AND ITS ONE LEAK

The worksheet carries no arm label, no original label and no case id. It is
shuffled with a recorded seed and keyed by an opaque hash.

The leak, stated because it cannot be closed without withholding evidence the
adjudicator needs: the `trusted_metrics` column IS the payload, and the two arms
have structurally different payloads -- the structured arm carries `totals` and
`change_over_period` keys the baseline arm does not. An adjudicator who knows
the system can infer the arm from shape. Removing those keys would hide the very
facts against which faithfulness is judged, which would be worse. Treat the
blinding as protecting against label leakage, not arm leakage.
"""

from __future__ import annotations

import csv
import hashlib
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from rrip.ai import narration as narration_mod  # noqa: E402
from rrip.ai.derive import derive  # noqa: E402
from rrip.ai.provider import CACHE_DIR, GeminiProvider  # noqa: E402
from rrip.eval.narration_cases import CASES  # noqa: E402

HERE = Path(__file__).resolve().parent
SHUFFLE_SEED = 20260814
HUMAN_SAMPLE_SIZE = 50

# The seven outputs flagged as failures by the original classifier's FIRST run.
# They are force-included in the human subset so the audit covers the cases the
# earlier investigation already touched, alongside a random sample of the rest.
ORIGINALLY_FLAGGED = {
    ("NAR-031", "baseline"), ("NAR-061", "structured"), ("NAR-062", "baseline"),
    ("NAR-150", "structured"), ("NAR-160", "baseline"), ("NAR-162", "structured"),
    ("NAR-174", "baseline"),
}


def build_payload(case, path: str) -> dict:
    rows, columns = case.materialise()
    if path == "structured":
        return derive(rows, columns, case.question).data
    return {"columns": columns, "rows": rows}


def build_prompt(case, payload: dict) -> str:
    """Reproduce rrip.ai.narration.narrate's prompt exactly."""
    blob = json.dumps(payload, default=str, indent=2)[:12000]
    return (f"QUESTION\n{case.question}\n\nDATA\n{blob}\n\n"
            "Return the JSON described in the system instructions.")


def cache_lookup(prompt: str, system: str) -> str | None:
    """Find the cached response, trying every model name in the fallback chain.

    The cache key includes the provider's CURRENT model attribute, and
    GeminiProvider rewrites that attribute to whichever model actually answered.
    So entries written during one run can be keyed under different model names
    depending on when the fallback advanced. Trying the whole chain is what makes
    recovery reliable rather than lucky.
    """
    for model in GeminiProvider.MODELS:
        key = hashlib.sha256(
            f"gemini|{model}|{system}|{prompt}".encode()).hexdigest()[:24]
        path = CACHE_DIR / f"gemini-{key}.json"
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))["text"]
            except (OSError, ValueError, KeyError):
                continue
    return None


def flatten(raw: str) -> tuple[str, bool]:
    """Parse the model's JSON and render one claim per line.

    Returns (text, parsed_ok). One line per field means no two claims can be
    read as a single sentence -- the defect that produced a false failure when
    the classifier joined fields with a bare space.
    """
    import re

    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.IGNORECASE)
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return raw.strip(), False
    if not isinstance(obj, dict):
        return raw.strip(), False

    lines = []
    if obj.get("headline"):
        lines.append(str(obj["headline"]).strip())
    for ins in obj.get("insights") or []:
        if not isinstance(ins, dict):
            continue
        if ins.get("statement"):
            lines.append(str(ins["statement"]).strip())
        if ins.get("so_what"):
            lines.append(str(ins["so_what"]).strip())
    for c in obj.get("caveats") or []:
        lines.append(str(c).strip())
    return "\n".join(x for x in lines if x), True


def main() -> int:
    system = narration_mod.SYSTEM
    rows_out, key_out, missing = [], [], []

    for case in CASES:
        for path in ("baseline", "structured"):
            payload = build_payload(case, path)
            raw = cache_lookup(build_prompt(case, payload), system)
            if raw is None:
                missing.append((case.id, path))
                continue
            narration, parsed = flatten(raw)
            row_id = hashlib.sha256(
                f"{case.id}|{path}|{narration}".encode()).hexdigest()[:12]
            rows_out.append({
                "row_id": row_id,
                "question": case.question,
                "trusted_metrics": json.dumps(payload, default=str,
                                              sort_keys=True),
                "narration": narration,
                "label": "",
                "category": "",
                "note": "",
            })
            key_out.append({
                "row_id": row_id,
                "case_id": case.id,
                "arm": path,
                "case_category": case.category,
                "parsed_json_ok": parsed,
                "originally_flagged": (case.id, path) in ORIGINALLY_FLAGGED,
            })

    rng = random.Random(SHUFFLE_SEED)
    order = list(range(len(rows_out)))
    rng.shuffle(order)
    rows_out = [rows_out[i] for i in order]
    by_id = {k["row_id"]: k for k in key_out}

    with (HERE / "adjudication_worksheet.csv").open(
            "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows_out[0].keys()))
        w.writeheader()
        w.writerows(rows_out)

    with (HERE / "adjudication_key.csv").open(
            "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=[
            "row_id", "case_id", "arm", "case_category", "parsed_json_ok",
            "originally_flagged"])
        w.writeheader()
        w.writerows([by_id[r["row_id"]] for r in rows_out])

    # --- stratified human subset -------------------------------------------
    flagged = [r for r in rows_out if by_id[r["row_id"]]["originally_flagged"]]
    rest = [r for r in rows_out if not by_id[r["row_id"]]["originally_flagged"]]
    rng2 = random.Random(SHUFFLE_SEED)
    sample = rng2.sample(rest, min(HUMAN_SAMPLE_SIZE - len(flagged), len(rest)))
    human = flagged + sample
    rng2.shuffle(human)

    with (HERE / "adjudication_worksheet_human.csv").open(
            "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows_out[0].keys()))
        w.writeheader()
        w.writerows(human)

    print(f"cases                 : {len(CASES)}")
    print(f"outputs recovered     : {len(rows_out)} / {len(CASES) * 2}")
    print(f"missing from cache    : {len(missing)} {missing[:6]}")
    print(f"json parse failures   : {sum(1 for k in key_out if not k['parsed_json_ok'])}")
    print(f"shuffle seed          : {SHUFFLE_SEED}")
    print(f"human subset          : {len(human)} "
          f"({len(flagged)} originally flagged + {len(sample)} random)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
