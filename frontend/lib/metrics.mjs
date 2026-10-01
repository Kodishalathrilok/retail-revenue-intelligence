/**
 * What each figure on the site means: definition, source, calculation,
 * limitation. This is the content of the "Explain" interaction.
 *
 * Static on purpose -- no language model writes any of it. Every entry
 * restates what the API's SQL (src/rrip/api/queries.py) and the estimators
 * actually do, and tests/test_overview_product.py fails if an entry names an
 * endpoint the API does not serve or a table its SQL does not read.
 *
 * `tables` lists the published table the demo reads first, then the table it
 * was built from.
 */

/**
 * @typedef {{label: string, definition: string, source: string, endpoint: string,
 *            tables: string[], calculation: string, limitation: string}} Metric
 */

/** @type {Record<string, Metric>} */
export const METRICS = {
  revenue: {
    label: 'Revenue',
    definition: 'The net amount households were charged at the till, after retailer discounts.',
    source: 'The sales_value of every transaction in the panel. The published demo reads it pre-summed.',
    endpoint: '/api/v1/overview',
    tables: ['pub_overview_totals', 'fact_transactions'],
    calculation: 'Sum of sales_value over all transactions, or over one department when a department is chosen.',
    limitation: "A panel of 2,500 households, not a retailer's total sales. Returns are recorded as quantity ≤ 0, never as negative money.",
  },
  baskets: {
    label: 'Baskets',
    definition: 'The number of shopping trips. One basket is one checkout.',
    source: 'The basket_id on each transaction, counted once per trip.',
    endpoint: '/api/v1/overview',
    tables: ['pub_overview_totals', 'fact_transactions'],
    calculation: 'Count of distinct basket_id.',
    limitation: 'With a department chosen, a trip counts if it includes at least one item from that department, so department baskets add up to more than the total.',
  },
  households: {
    label: 'Households',
    definition: 'Households that made at least one purchase.',
    source: 'The household_key on each transaction, counted once per household.',
    endpoint: '/api/v1/overview',
    tables: ['pub_overview_totals', 'fact_transactions'],
    calculation: 'Count of distinct household_key.',
    limitation: 'Households joined the panel over its first months, so the early weeks have far fewer of them. On the published demo the count always covers the whole 711 days.',
  },
  avg_basket: {
    label: 'Average basket',
    definition: 'The average amount spent per shopping trip.',
    source: 'Revenue and baskets, as defined here.',
    endpoint: '/api/v1/overview',
    tables: ['pub_overview_totals', 'fact_transactions'],
    calculation: 'Revenue divided by the number of distinct baskets.',
    limitation: 'A mean: a few very large trips pull it up, and it says nothing about the typical trip.',
  },
  units: {
    label: 'Units',
    definition: 'Items sold, counted by quantity.',
    source: 'The quantity on each transaction.',
    endpoint: '/api/v1/overview',
    tables: ['pub_overview_totals', 'fact_transactions'],
    calculation: 'Sum of quantity, leaving out returns and goods sold by weight.',
    limitation: '23,101 rows record weighted goods in grams. They are excluded because grams and items cannot be added.',
  },
  weekly_revenue: {
    label: 'Weekly revenue',
    definition: 'Revenue in each panel week, with a seven-week centred average to show the trend.',
    source: 'Weekly totals for all departments or for one department, with week dates from the week calendar.',
    endpoint: '/api/v1/revenue/weekly',
    tables: ['pub_weekly_revenue', 'pub_weekly_revenue_by_dept', 'pub_dim_week', 'fact_transactions'],
    calculation: 'Sum of sales_value per week. The trend is the mean of each week with the three weeks before and the three after.',
    limitation: 'Weeks before 20 reflect households still joining the panel, not rising demand. Weeks 1 and 102 are partial (5 and 6 days). At both ends the average covers fewer than seven weeks.',
  },
  flagged_weeks: {
    label: 'Flagged weeks',
    definition: 'Weeks whose revenue is unusually far from the average week.',
    source: 'A z-score for every week, computed from weekly revenue across all departments.',
    endpoint: '/api/v1/ai/anomalies',
    tables: ['pub_anomalies', 'fact_transactions'],
    calculation: 'z-score = (week revenue − mean) ÷ standard deviation, with the mean and standard deviation taken over full weeks. A week is flagged when its z-score is at least 2.5 in either direction.',
    limitation: 'A statistical rule, not a judgement about cause. The mean includes the enrolment weeks, so the early ramp is what stands out and later swings must be very large to be flagged.',
  },
  rfm_segments: {
    label: 'Customer segments',
    definition: 'Households grouped by how recently, how often and how much they bought (RFM).',
    source: 'One row per segment, built from every household’s purchase history.',
    endpoint: '/api/v1/segments/rfm',
    tables: ['pub_rfm_segments', 'fact_transactions'],
    calculation: 'Each household is scored 1–5 on recency, number of baskets and total spend, then placed in a segment by fixed rules. Share of revenue is the segment’s spend divided by all spend.',
    limitation: 'Recency is measured against the last day of the panel, not today. Segments describe past behaviour; they do not predict it.',
  },
  forecast: {
    label: 'Next-week forecast',
    definition: 'Expected revenue for one department in the next panel week, with the range the actual figure is likely to fall in.',
    source: 'The forecasting pipeline’s output, scored on weeks it never saw.',
    endpoint: '/api/v1/forecast',
    tables: ['pub_forecast', 'pub_forecast_summary'],
    calculation: 'The mean of the department’s last four completed weeks. The range is an 80% prediction interval, calibrated on held-out weeks.',
    limitation: 'One week ahead only. An average of recent weeks cannot anticipate a promotion, a spike or a level shift. A machine-learning model was tested and did not beat it.',
  },
  campaign_effect: {
    label: 'Campaign effect',
    definition: 'The estimated change in weekly spend per household for households in a campaign, compared with households that were not.',
    source: 'Household spending panels built in SQL; the estimate comes from a statistical model, not a language model.',
    endpoint: '/api/v1/causal/analysis/{campaign_id}',
    tables: ['pub_causal_results', 'pub_causal_pretrend'],
    calculation: 'Difference-in-differences: the campaign group’s before-to-after change minus the comparison group’s change over the same weeks. The range is a 95% confidence interval.',
    limitation: 'Campaigns were targeted, not randomised, so this is an estimate and not proof. It holds only if both groups would otherwise have moved in parallel, and the test for that has low power. An interval that includes zero does not show there was no effect.',
  },
};

/** @param {string} key */
export function metric(key) {
  const m = METRICS[key];
  if (!m) throw new Error(`unknown metric: ${key}`);
  return m;
}
