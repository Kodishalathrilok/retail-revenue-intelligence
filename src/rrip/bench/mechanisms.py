"""Verify that each benchmark query is expensive for the reason it claims.

Each query in sql/perf/ predicts a specific plan signature in its header. This
module checks the recorded plan against that prediction and reports the answer
either way.

The point is the negative case. If a query turns out to be expensive for a
different reason than predicted, that is a finding to report — not a prompt to
reshape the query until it matches its label. These functions therefore return
evidence extracted from the plan, not a pass/fail verdict alone, so a mismatch
can be described accurately.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any


def walk(node: dict) -> Iterator[dict]:
    yield node
    for child in node.get("Plans", []) or []:
        yield from walk(child)


def _relations(plan: dict, prefix: str) -> list[str]:
    return [n.get("Relation Name", "") for n in walk(plan)
            if str(n.get("Relation Name", "")).startswith(prefix)]


def _max_estimate_error(plan: dict) -> tuple[float, dict | None]:
    """Largest actual/estimated row ratio across all nodes, and the node."""
    worst, worst_node = 1.0, None
    for n in walk(plan):
        est = float(n.get("Plan Rows") or 0)
        act = float(n.get("Actual Rows") or 0)
        loops = float(n.get("Actual Loops") or 1)
        act_total = act * loops
        if est <= 0:
            continue
        ratio = max(act_total / est, est / max(act_total, 1))
        if ratio > worst:
            worst, worst_node = ratio, n
    return worst, worst_node


def check_q1(plan: dict, rec: dict) -> tuple[bool, str]:
    """Predicted: partition pruning fails; all 102 fact_causal partitions scanned."""
    parts = _relations(plan, "fact_causal_w")
    removed = sum(int(n.get("Subplans Removed") or 0) for n in walk(plan))
    ok = len(parts) >= 100 and removed == 0
    return ok, (f"{len(parts)} of 102 fact_causal partitions in plan, "
                f"Subplans Removed={removed}")


def check_q2(plan: dict, rec: dict) -> tuple[bool, str]:
    """Predicted: aggregate or sort spills to disk."""
    methods = [n.get("Sort Method", "") for n in walk(plan) if n.get("Sort Method")]
    batches = max((int(n.get("HashAgg Batches") or 0) for n in walk(plan)), default=0)
    disk_kb = max((int(n.get("Disk Usage") or 0) for n in walk(plan)), default=0)
    temp_w = int(rec.get("temp_written") or 0)
    spilled = temp_w > 0 or batches > 1 or any("external" in m for m in methods)
    return spilled, (f"temp_written={temp_w} blocks, HashAgg batches={batches}, "
                     f"disk usage={disk_kb}kB, sort methods={methods or 'none'}")


def check_q3(plan: dict, rec: dict) -> tuple[bool, str]:
    """Predicted: seq scan on dim_product; upper() defeats the btree index."""
    prod_nodes = [n for n in walk(plan) if n.get("Relation Name") == "dim_product"]
    node_types = [n.get("Node Type") for n in prod_nodes]
    used_index = any("Index" in str(t) for t in node_types)
    has_upper = any("upper" in str(n.get("Filter", "")) for n in prod_nodes)
    ok = (not used_index) and has_upper
    return ok, (f"dim_product accessed via {node_types or 'not scanned'}; "
                f"upper() in filter: {has_upper}")


def check_q4(plan: dict, rec: dict) -> tuple[bool, str]:
    """Predicted: correlated columns cause a >10x row misestimate."""
    ratio, node = _max_estimate_error(plan)
    where = f"{node.get('Node Type')} on {node.get('Relation Name', 'n/a')}" if node else "n/a"
    est = node.get("Plan Rows") if node else 0
    act = (node.get("Actual Rows") or 0) * (node.get("Actual Loops") or 1) if node else 0
    return ratio >= 10, (f"worst estimate error {ratio:,.1f}x at {where} "
                         f"(estimated {est:,}, actual {act:,.0f})")


def check_q5(plan: dict, rec: dict) -> tuple[bool, str]:
    """Predicted: fact_transactions scanned more than once per call."""
    scans = _relations(plan, "fact_transactions")
    distinct_parts = len(set(scans))
    return len(scans) > distinct_parts, (
        f"{len(scans)} scan nodes over {distinct_parts} distinct "
        f"fact_transactions partitions (repeat factor "
        f"{len(scans) / max(distinct_parts, 1):.1f}x)")


CHECKS = {
    "q1": (check_q1, "partition pruning failure"),
    "q2": (check_q2, "sort/hash spill to disk"),
    "q3": (check_q3, "index-unusable predicate"),
    "q4": (check_q4, "join-order misestimate"),
    "q5": (check_q5, "repeated full-scan aggregation"),
}


def verify(query_name: str, plan_json: Any, rec: dict) -> tuple[str, bool, str]:
    """Return (claimed_mechanism, matched, evidence)."""
    key = query_name.split("_")[0]
    if key not in CHECKS:
        return "unknown", False, "no mechanism check registered"
    fn, claimed = CHECKS[key]
    root = plan_json[0] if isinstance(plan_json, list) else plan_json
    plan = root["Plan"] if "Plan" in root else root
    try:
        matched, evidence = fn(plan, rec)
    except Exception as exc:  # a broken check must not look like a mismatch
        return claimed, False, f"check raised {type(exc).__name__}: {exc}"
    return claimed, matched, evidence
