# Business insights brief — dunnhumby Complete Journey panel

Five findings a retail analyst can act on, each measured from the published
aggregate tier (the `pub_*` tables the live demo reads). Every figure here was
produced by a read-only SQL query against those tables — not estimated, not
illustrative. The population throughout is the panel of **2,500 households over
711 days (102 weeks)**, which is a research panel, not a retailer's total sales.

Sources: `pub_rfm_segments`, `pub_headline`, `pub_reorder_by_department`,
`pub_commodity_affinity`, `pub_causal_results` (`src/rrip/publish/tables.py`
builds them; the dashboard reads them through the read-only API).

---

## 1. Revenue is concentrated in a small, high-value core

**Measured.** The **Champions** RFM segment is **512 households — 20.5% of the
panel — but earns 45.6% of revenue** (average lifetime value **$7,181**, average
244.7 baskets, last purchase ~1 day before panel end). Add **Loyal** (25.7% of
households) and two segments — 46% of households — account for **73% of
revenue**. At the other end, **Lapsed** is 26.2% of households but 7.3% of
revenue (avg lifetime value $897).

**Why it matters.** Revenue depends on keeping a small group. A lost Champion is
worth roughly eight lost Lapsed households, so retention spend is best aimed at
the top, not spread evenly.

**Recommended action.** Give Champions and Loyal explicit retention treatment
(service, early access, win-back triggers on a recency slip); measure churn in
these segments specifically rather than panel-wide.

**Assumptions & limitations.** RFM is descriptive of past behaviour; recency is
measured against the panel's last day, not today. Segment boundaries are fixed
score thresholds. Panel ≠ retailer total.

---

## 2. The catalogue is a long tail — a few SKUs make most of the money

**Measured.** **11,865 products — 12.9% of the ~92,000-product catalogue —
produce 80% of revenue.** The other ~87% of SKUs together make the last 20%.

**Why it matters.** The head of the catalogue is the business; the deep tail
consumes shelf, working capital and operational attention out of proportion to
what it earns.

**Recommended action.** Protect availability and forecast accuracy on the head
SKUs first. Review the deep tail for rationalisation, keeping only items with a
clear traffic-driver or basket-completion role.

**Assumptions & limitations.** Ranked on net `sales_value`; a panel sample, so
absolute SKU ranks are indicative. Revenue share alone does not capture an
item's role in completing a trip or drawing a visit.

---

## 3. Perishables are the repeat-purchase engine; non-food is occasional

**Measured.** Average household reorder rate by department: **SALAD BAR 38.7%,
SEAFOOD 32.5%, PRODUCE 31.8%** at the top; **FLORAL 14.9%** (lowest), PASTRY
22.8% below the fresh departments.

**Why it matters.** Habitual, repeated purchases come from fresh/perishable
categories. A freshness or out-of-stock failure there does not just lose one
sale — it interrupts a recurring trip.

**Recommended action.** Prioritise in-stock and freshness SLAs for the
high-reorder perishable departments. Treat low-reorder departments (e.g.
FLORAL) as occasion-driven and merchandise them that way, not as habit builders.

**Assumptions & limitations.** Reorder rate is the share of a household's
products bought again (per the published definition), averaged across
households in a department. Department-level; panel sample.

---

## 4. A handful of category pairs co-purchase far above chance

**Measured.** Highest-lift commodity pairs (restricted to pairs sharing ≥500
baskets, so the lift is not a small-sample artefact):

| Pair | Shared baskets | Lift |
|---|---:|---:|
| FROZEN ↔ REFRIGERATED | 619 | 19.7× |
| CAT FOOD ↔ CAT LITTER | 1,292 | 16.9× |
| BABY FOODS ↔ INFANT FORMULA | 718 | 16.5× |
| SEAFOOD-FROZEN ↔ SEAFOOD-MISC | 521 | 15.5× |
| BABY HBC ↔ DIAPERS & DISPOSABLES | 1,100 | 15.4× |

A lift of 16.9 means cat-food and cat-litter appear together about **17× more
often than if the two were bought independently**.

**Why it matters.** These are coherent "mission" baskets (pet, baby, a frozen/
chilled shop). They are the natural targets for adjacency, bundling and
cross-category coupons.

**Recommended action.** Co-locate or bundle-coupon the high-lift mission pairs
(pet and baby first). Test aisle adjacency and a "forgot the litter?" reminder
at checkout for cat-food baskets.

**Assumptions & limitations.** Lift is **associational, not causal** — it says
the pair co-occurs, not that one drives the other. Commodity-level, not SKU; a
≥500-basket support filter is applied; panel sample.

---

## 5. The cleanest measurable campaign shows no reliable lift

**Measured.** Campaign 26 is the least-contaminated campaign in the dataset
(310 treated households vs 2,140 control, 6.6% contaminated). Its
difference-in-differences estimate is **+$1.51 per household per week, 95%
confidence interval −$2.35 to +$5.36, p = 0.444** — the evidence label is
**WEAK**.

**Why it matters.** The interval includes zero (and sizeable positive values):
the data cannot confirm this campaign moved spending. Reporting a point
"+$1.51" as if it were a result would overstate what is known.

**Recommended action.** Do not attribute revenue to this campaign. Design future
campaigns with a proper holdout or randomisation so the effect can be read
cleanly, and treat all targeted-campaign results as estimates with intervals,
not single numbers.

**Assumptions & limitations.** Observational — campaigns were targeted, not
randomised, so difference-in-differences relies on a parallel-trends assumption
whose test is low-power. The panel measures spend **conditional on shopping that
week** (see `docs/methodology-notes.md` §9), so this is an intensive-margin
estimate.

---

*Reproduce any figure by querying the corresponding `pub_*` table as the
read-only role (`RRIP_PG_READONLY_DSN`); the dashboard surfaces findings 1 and
the retention view under the **Customers** tab, finding 5 under **Causal**.*
