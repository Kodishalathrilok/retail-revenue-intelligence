"""Phase 0 -- dataset probe.

Measures the dunnhumby files before any schema is written, to settle three
questions that would otherwise surface too late:

  1. Actual row counts (for honest scale claims in the README).
  2. How large causal_data really is (decides the Instacart appendix).
  3. Whether any campaign supports a real difference-in-differences (Phase 6c).

Plus data-quality reconnaissance that shapes the Phase 1 grain and key choices.
Writes docs/dataset-profile.md from real measurements only.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from rich.console import Console
from rich.table import Table

from rrip.profile import campaigns as campaign_analysis
from rrip.profile.discovery import count_rows, discover, read_csv

console = Console()

TX_DTYPES = {
    "household_key": "int32",
    "basket_id": "int64",
    "day": "int16",
    "product_id": "int32",
    "quantity": "int32",
    "sales_value": "float32",
    "store_id": "int32",
    "retail_disc": "float32",
    "trans_time": "int16",
    "week_no": "int16",
    "coupon_disc": "float32",
    "coupon_match_disc": "float32",
}


def _fmt(n: int) -> str:
    return f"{n:,}"


def scan_causal(path: Path) -> dict:
    """Chunk-scan causal_data: too large to hold on 8 GB, so aggregate as we go."""
    rows = 0
    weeks: set[int] = set()
    products: set[int] = set()
    stores: set[int] = set()
    display_vals: set = set()
    mailer_vals: set = set()

    # display/mailer are categorical codes mixing digits and letters ('0'..'9',
    # 'A'..'Z'). Without an explicit dtype pandas infers int for some chunks and
    # str for others, which both warns and corrupts the distinct-value set.
    dtypes = {"display": "string", "mailer": "string"}
    for chunk in pd.read_csv(path, chunksize=2_000_000, dtype=dtypes):
        chunk.columns = [c.strip().lower() for c in chunk.columns]
        rows += len(chunk)
        if "week_no" in chunk:
            weeks.update(chunk["week_no"].unique().tolist())
        if "product_id" in chunk:
            products.update(chunk["product_id"].unique().tolist())
        if "store_id" in chunk:
            stores.update(chunk["store_id"].unique().tolist())
        if "display" in chunk:
            display_vals.update(chunk["display"].dropna().unique().tolist())
        if "mailer" in chunk:
            mailer_vals.update(chunk["mailer"].dropna().unique().tolist())

    return {
        "rows": rows,
        "weeks": (min(weeks), max(weeks)) if weeks else None,
        "distinct_products": len(products),
        "distinct_stores": len(stores),
        "display_values": sorted(map(str, display_vals)),
        "mailer_values": sorted(map(str, mailer_vals)),
    }


def quality_checks(tx: pd.DataFrame, products: pd.DataFrame | None,
                   demo: pd.DataFrame | None) -> list[dict]:
    """Findings that change Phase 1 modelling decisions."""
    checks: list[dict] = []

    def add(name: str, value, note: str) -> None:
        checks.append({"check": name, "value": value, "implication": note})

    n = len(tx)
    add("transaction rows", _fmt(n), "fact_transactions grain candidate")
    add("distinct households", _fmt(tx["household_key"].nunique()), "dim_household size")
    add("distinct baskets", _fmt(tx["basket_id"].nunique()), "degenerate dimension")
    add("distinct products in tx", _fmt(tx["product_id"].nunique()), "dim_product usage")
    add("distinct stores", _fmt(tx["store_id"].nunique()), "dim_store size")

    day_min, day_max = int(tx["day"].min()), int(tx["day"].max())
    add("day range", f"{day_min} - {day_max}", "dim_date span")
    add("week range", f"{int(tx['week_no'].min())} - {int(tx['week_no'].max())}",
        "partition range for fact_causal")

    # How does WEEK_NO relate to DAY? The panel does not start on a week
    # boundary -- week 1 is a partial week -- so the naive ceil(day/7) is wrong.
    # Getting this right determines the dim_date anchor.
    naive = ((tx["day"] - 1) // 7) + 1
    offset = (tx["day"] + 8) // 7
    add("week_no != ceil(day/7) [naive]", _fmt(int((naive != tx["week_no"]).sum())),
        "non-zero means the panel does not start on a week boundary")
    add("week_no != (day+8)//7 [offset]", _fmt(int((offset != tx["week_no"]).sum())),
        "0 confirms week 1 is partial and day 6 starts the first full week")

    wk1 = tx.loc[tx["week_no"] == 1, "day"]
    if not wk1.empty:
        add("week 1 day span", f"{int(wk1.min())}-{int(wk1.max())}",
            "a short week 1 fixes the weekday anchor: day 6 is a Monday, so day 1 is a Wednesday")

    # Grain integrity -- decides whether (basket_id, product_id) can be the PK.
    dupes = int(tx.duplicated(subset=["basket_id", "product_id"]).sum())
    add("duplicate (basket_id, product_id)", _fmt(dupes),
        "non-zero forces a surrogate key; do NOT dedupe real transactions")

    # Money columns. Sign conventions here decide gross vs net revenue.
    neg_sales = int((tx["sales_value"] < 0).sum())
    zero_sales = int((tx["sales_value"] == 0).sum())
    add("sales_value < 0", _fmt(neg_sales), "returns/refunds; must not be silently dropped")
    add("sales_value == 0", _fmt(zero_sales), "free goods / full coupon coverage")

    if "retail_disc" in tx:
        neg_disc = int((tx["retail_disc"] < 0).sum())
        pos_disc = int((tx["retail_disc"] > 0).sum())
        add("retail_disc sign", f"{_fmt(neg_disc)} neg / {_fmt(pos_disc)} pos",
            "establishes whether gross = sales_value - retail_disc")

    qty_le0 = int((tx["quantity"] <= 0).sum())
    qty_big = int((tx["quantity"] > 1000).sum())
    add("quantity <= 0", _fmt(qty_le0), "returns or data errors")
    add("quantity > 1000", _fmt(qty_big), "weighted goods recorded in grams, not units")

    if products is not None and "product_id" in products:
        known = set(products["product_id"])
        orphans = int((~tx["product_id"].isin(known)).sum())
        add("tx products missing from product.csv", _fmt(orphans),
            "referential integrity; non-zero needs an unknown-member row in dim_product")

    if demo is not None and "household_key" in demo:
        covered = tx["household_key"].isin(set(demo["household_key"])).sum()
        hh_covered = len(set(demo["household_key"]) & set(tx["household_key"]))
        hh_total = tx["household_key"].nunique()
        add("households with demographics", f"{hh_covered} / {hh_total}"
            f" ({100 * hh_covered / max(hh_total, 1):.1f}%)",
            "limits which confounders Phase 6c can adjust for")
        add("transactions covered by demographics",
            f"{100 * covered / max(len(tx), 1):.1f}%",
            "demographic slices cover only part of the panel")

    return checks


def build_report(
    file_counts: dict[str, tuple[Path, int]],
    causal_stats: dict | None,
    checks: list[dict],
    camp: pd.DataFrame,
    blanket_note: str,
) -> str:
    ts = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    L: list[str] = []
    L.append("# Dataset profile -- dunnhumby *The Complete Journey*\n")
    L.append(f"Generated {ts} by `rrip profile` from a real run over the raw CSVs. "
             "Every number below is measured, not estimated.\n")

    L.append("## File inventory\n")
    L.append("| File | Path | Data rows |")
    L.append("|---|---|---:|")
    for name, (path, rows) in sorted(file_counts.items()):
        L.append(f"| `{name}` | `{path.name}` | {_fmt(rows)} |")
    L.append("")

    if causal_stats:
        L.append("## causal_data scale\n")
        L.append(f"- **Rows:** {_fmt(causal_stats['rows'])}")
        if causal_stats["weeks"]:
            L.append(f"- Week range: {causal_stats['weeks'][0]}-{causal_stats['weeks'][1]}")
        L.append(f"- Distinct products: {_fmt(causal_stats['distinct_products'])}")
        L.append(f"- Distinct stores: {_fmt(causal_stats['distinct_stores'])}")
        L.append(f"- `display` values: {causal_stats['display_values']}")
        L.append(f"- `mailer` values: {causal_stats['mailer_values']}")
        L.append("")

    L.append("## Data quality findings\n")
    L.append("| Check | Value | Why it matters |")
    L.append("|---|---|---|")
    for c in checks:
        L.append(f"| {c['check']} | {c['value']} | {c['implication']} |")
    L.append("")

    L.append("## Campaign viability for difference-in-differences\n")
    L.append(f"Screens: pre-period >= {campaign_analysis.MIN_PRE_DAYS}d, "
             f"post-period >= {campaign_analysis.MIN_POST_DAYS}d, "
             f"treated >= {campaign_analysis.MIN_TREATED}, "
             f"control >= {campaign_analysis.MIN_CONTROL} households.\n")
    L.append(f"The analysis window is a bounded {campaign_analysis.PRE_WINDOW_DAYS}-day "
             "pre-period through campaign end.\n")
    L.append("- **treated_n** -- households enrolled in this campaign.\n"
             "- **clean_treated_n** -- those *not* also enrolled in an overlapping "
             "campaign. This is the treatment group Phase 6c would use, and the one the "
             "size screen applies to: an estimate on the full enrolled set measures this "
             "campaign plus whatever else those households were in.\n"
             "- **control_n** -- households not in this campaign and not in any campaign "
             "overlapping its analysis window.\n"
             "- **strict_control_n** -- households in *no* campaign at all. Cleaner where "
             "it exists, but a single blanket campaign can empty it without invalidating "
             "the comparison, so it does not drive the verdict.\n")
    L.append(blanket_note + "\n")

    cols = ["campaign", "type", "start_day", "end_day", "pre_days", "post_days",
            "treated_n", "clean_treated_n", "contaminated_pct", "control_n",
            "strict_control_n", "overlapping_campaigns", "pre_weeks", "slope_gap",
            "viable", "reasons"]
    L.append("| " + " | ".join(cols) + " |")
    L.append("|" + "---|" * len(cols))
    for _, r in camp.iterrows():
        L.append("| " + " | ".join(str(r[c]) for c in cols) + " |")
    L.append("")

    n_viable = int(camp["viable"].sum())
    L.append("## Verdict\n")
    if n_viable == 0:
        L.append("**No campaign passes all four screens.** Phase 6c cannot be built as "
                 "specified without redesign. See the `reasons` column for what fails.\n")
    else:
        best = camp[camp["viable"]].iloc[0]
        L.append(f"**{n_viable} campaign(s) viable.** Cleanest candidate: "
                 f"campaign {best['campaign']} ({best['type']}) -- "
                 f"{best['pre_days']}d pre-period, {best['clean_treated_n']} "
                 f"uncontaminated treated (of {best['treated_n']} enrolled, "
                 f"{best['contaminated_pct']}% contaminated) vs "
                 f"{best['control_n']} control.\n")
        L.append("`slope_gap` is the difference in pre-period weekly-spend trend between "
                 "groups. It is a smell test, not a parallel-trends test -- Phase 6c must "
                 "still run the formal check and report violations.\n")
    return "\n".join(L)


def run(raw_dir: Path, out_path: Path) -> pd.DataFrame:
    console.rule("[bold]Phase 0 -- dataset probe")

    files = discover(raw_dir)
    console.print(f"Discovered {len(files)} file(s) under [cyan]{raw_dir}[/cyan]\n")

    console.print("Counting rows (streaming, no full load)...")
    file_counts = {name: (p, count_rows(p)) for name, p in files.items()}
    t = Table(title="File inventory")
    t.add_column("logical name")
    t.add_column("file")
    t.add_column("rows", justify="right")
    for name, (p, rows) in sorted(file_counts.items()):
        t.add_row(name, p.name, _fmt(rows))
    console.print(t)

    console.print("\nLoading transactions...")
    tx = read_csv(files["transactions"])
    for col, dt in TX_DTYPES.items():
        if col in tx.columns:
            try:
                tx[col] = tx[col].astype(dt)
            except (ValueError, TypeError, OverflowError):
                console.print(f"  [yellow]could not cast {col} to {dt}; leaving as-is")
    console.print(f"  {_fmt(len(tx))} rows, "
                  f"{tx.memory_usage(deep=True).sum() / 1024**2:.0f} MB in memory")

    products = read_csv(files["products"]) if "products" in files else None
    demo = read_csv(files["households"]) if "households" in files else None
    members = read_csv(files["campaign_members"])
    desc = read_csv(files["campaign_desc"])

    causal_stats = None
    if "causal" in files:
        console.print("\nScanning causal_data in chunks (this is the big one)...")
        causal_stats = scan_causal(files["causal"])
        console.print(f"  [bold]{_fmt(causal_stats['rows'])} rows[/bold]")

    console.print("\nRunning data quality checks...")
    checks = quality_checks(tx, products, demo)

    console.print("Analysing campaign viability...")
    camp = campaign_analysis.analyse(tx, members, desc)

    panel_n = tx["household_key"].nunique()
    by_type = (
        members.groupby("description")["household_key"].nunique()
        if "description" in members else pd.Series(dtype=int)
    )
    reach = ", ".join(
        f"**{k}** {_fmt(v)} ({100 * v / max(panel_n, 1):.0f}%)" for k, v in by_type.items()
    ) or "n/a"
    blanket_note = (
        f"Household reach by campaign type (vs {_fmt(panel_n)} panel households): {reach}"
    )

    vt = Table(title="Campaign viability (ranked by contamination)")
    for c in ["campaign", "type", "treated", "clean", "contam%", "control", "viable"]:
        vt.add_column(c, overflow="fold")
    for _, r in camp.head(12).iterrows():
        vt.add_row(
            str(r["campaign"]), str(r["type"]), str(r["treated_n"]),
            str(r["clean_treated_n"]), f"{r['contaminated_pct']}",
            str(r["control_n"]),
            "[green]yes" if r["viable"] else "[red]no",
        )
    console.print(vt)

    report = build_report(file_counts, causal_stats, checks, camp, blanket_note)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")
    console.print(f"\n[green]Wrote[/green] {out_path}")

    n_viable = int(camp["viable"].sum())
    if n_viable == 0:
        console.print("\n[bold red]No viable campaign for DiD. Phase 6c needs redesign.")
    else:
        console.print(f"\n[bold green]{n_viable} viable campaign(s) for DiD.")
    return camp
