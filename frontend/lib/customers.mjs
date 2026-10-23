/**
 * Deterministic shaping for the Customer analytics page.
 *
 * Pure functions over the /api/v1/segments/rfm and /api/v1/retention/tenure
 * responses. Every figure these touch is a value the API returned; nothing
 * here invents a number. Plain JavaScript (.mjs, JSDoc types) so `node --test`
 * exercises it with no build step -- tests/test_frontend_logic.py runs
 * lib/customers.test.mjs.
 */

// Low -> high, so the retention legend and lines read in a sensible order
// regardless of the order the API returns the groups in.
const TERCILE_ORDER = ['Smallest first basket', 'Middle', 'Largest first basket'];

/**
 * Shape retention/tenure rows into one row per tenure month with a column per
 * onboarding group, plus the ordered group list, for a multi-line chart.
 *
 * @param {{segment: string, tenure_month: number|string, retention_pct: number|string}[]} items
 * @returns {{data: Record<string, number|null>[], segments: string[]}}
 */
export function retentionSeries(items) {
  const bySeg = new Map();
  const months = new Set();
  for (const it of items ?? []) {
    const seg = it.segment;
    const m = Number(it.tenure_month);
    const pct = Number(it.retention_pct);
    if (!seg || !Number.isFinite(m) || !Number.isFinite(pct)) continue;
    months.add(m);
    if (!bySeg.has(seg)) bySeg.set(seg, new Map());
    bySeg.get(seg).set(m, pct);
  }
  const known = (s) => (TERCILE_ORDER.indexOf(s) + 1 || 99);
  const segments = [...bySeg.keys()].sort((a, b) => known(a) - known(b) || a.localeCompare(b));
  const data = [...months].sort((a, b) => a - b).map((m) => {
    /** @type {Record<string, number|null>} */
    const row = { tenure_month: m };
    for (const seg of segments) row[seg] = bySeg.get(seg).get(m) ?? null;
    return row;
  });
  return { data, segments };
}

/**
 * Retention at one tenure month for each group, ordered low -> high. Used for
 * the section's headline sentence.
 *
 * @param {{segment: string, tenure_month: number|string, retention_pct: number|string}[]} items
 * @param {number} month
 * @returns {{segment: string, pct: number}[]}
 */
export function retentionAt(items, month) {
  const known = (s) => (TERCILE_ORDER.indexOf(s) + 1 || 99);
  return (items ?? [])
    .filter((it) => Number(it.tenure_month) === month)
    .map((it) => ({ segment: it.segment, pct: Number(it.retention_pct) }))
    .sort((a, b) => known(a.segment) - known(b.segment));
}

/**
 * The dominant RFM segment by share of revenue, with its concentration — the
 * gap between how much of the panel it is and how much of the revenue it earns.
 *
 * @param {{segment: string, pct_of_panel: number|string, pct_of_revenue: number|string}[]} segments
 * @returns {{segment: string, pctHouseholds: number, pctRevenue: number} | null}
 */
export function topSegment(segments) {
  const rows = segments ?? [];
  if (rows.length === 0) return null;
  const top = rows.reduce((a, b) =>
    (Number(b.pct_of_revenue) > Number(a.pct_of_revenue) ? b : a));
  return {
    segment: top.segment,
    pctHouseholds: Number(top.pct_of_panel),
    pctRevenue: Number(top.pct_of_revenue),
  };
}
