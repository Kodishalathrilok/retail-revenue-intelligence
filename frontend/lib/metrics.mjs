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
  forecast_range: {
    label: 'Forecast range',
    definition: 'The range next week’s actual revenue is expected to fall in. It is a prediction interval: a range for the actual figure, not for the average.',
    source: 'The lower and upper bound the forecast endpoint returns with every forecast.',
    endpoint: '/api/v1/forecast',
    tables: ['pub_forecast', 'pub_forecast_summary'],
    calculation: 'Split conformal prediction at the 80% level. The predictor’s misses on held-out validation weeks, each divided by the department’s recent weekly revenue, set how far below and above the forecast the bounds sit. The two sides can differ.',
    limitation: 'About one week in five is expected to land outside it. Coverage is measured across all departments together, not for each one, and it assumes the coming weeks behave like the weeks it was calibrated on.',
  },
  forecast_error: {
    label: 'Measured error',
    definition: 'How far forecasts were from what actually happened, as a share of revenue, on weeks the predictor never saw.',
    source: 'One-week-ahead forecasts for the held-out test weeks, compared with the actual revenue in those weeks.',
    endpoint: '/api/v1/forecast/departments',
    tables: ['pub_forecast_departments', 'pub_forecast'],
    calculation: 'WAPE: the sum of the absolute misses divided by the sum of actual revenue, as a percentage. The department figure uses that department’s test weeks; the pooled figure uses every department’s.',
    limitation: 'Weighted by dollars, so the largest departments dominate the pooled figure. Error differs widely between departments, and past error does not cap next week’s miss.',
  },
  forecast_confidence: {
    label: 'Confidence label',
    definition: 'How reliable forecasts for this department have measurably been: NORMAL, LIMITED or LOW.',
    source: 'The department’s measured error on the held-out test weeks, set against the pooled error for all departments.',
    endpoint: '/api/v1/forecast',
    tables: ['pub_forecast'],
    calculation: 'The department’s WAPE divided by the pooled WAPE. Up to 1.5 times is NORMAL, up to 3 times is LIMITED, and above that is LOW. A department with no recorded test error is LIMITED.',
    limitation: 'A label for past accuracy, not a probability. NORMAL means the department was not forecast much worse than the pooled figure; it does not mean the forecast is certain.',
  },
  forecast_method: {
    label: 'Method comparison',
    definition: 'Which forecasting method runs, and how it scored against the machine-learning model that was tested against it.',
    source: 'The model card: every method scored on the same held-out test weeks.',
    endpoint: '/api/v1/forecast/summary',
    tables: ['pub_forecast_summary'],
    calculation: 'The four-week trailing mean was selected on validation weeks. The machine-learning model would replace it only if its test error were lower and a Diebold-Mariano test found the difference significant at p < 0.05. Neither held, so the trailing mean runs.',
    limitation: 'A tie on one test window, not evidence that no model could do better. Some other simple methods scored lower on the test weeks; switching to one after seeing those scores would be choosing with hindsight.',
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
  campaign_interval: {
    label: '95% confidence interval',
    definition: 'The range of campaign effects that are consistent with the data at the 95% level.',
    source: 'Reported by the same regression that produces the estimate.',
    endpoint: '/api/v1/causal/analysis/{campaign_id}',
    tables: ['pub_causal_results'],
    calculation: 'The estimate plus and minus 1.96 standard errors.',
    limitation: 'An interval that includes zero does not show there was no effect: the data do not rule out a zero or small effect, and they do not rule out the larger values in the range either. It covers sampling uncertainty only, not bias from targeting or a failed assumption.',
  },
  campaign_stderr: {
    label: 'Standard error',
    definition: 'How much the estimate would be expected to vary from one sample of households to another. The interval and the p-value are built from it.',
    source: 'Reported by the same regression that produces the estimate.',
    endpoint: '/api/v1/causal/analysis/{campaign_id}',
    tables: ['pub_causal_results'],
    calculation: 'Cluster-robust standard errors, clustered by household: each household contributes many weeks, and its weeks are not independent observations.',
    limitation: 'Clustering accounts for repeated weeks from the same household, not for a comparison group that differs from the campaign group. A small standard error on a biased estimate is still a biased estimate.',
  },
  pretrend_test: {
    label: 'Pre-trend test',
    definition: 'A check of whether the campaign group and the comparison group were already moving apart in the weeks before the campaign.',
    source: 'Mean weekly spend per household for each group, over the pre-campaign weeks only.',
    endpoint: '/api/v1/causal/analysis/{campaign_id}',
    tables: ['pub_causal_results', 'pub_causal_pretrend'],
    calculation: 'A linear regression of the weekly group means on week, group and their interaction. An interaction p-value below 0.05 is treated as differing pre-treatment trends, and the estimate is then not attributed to the campaign.',
    limitation: 'Low power: one linear term fitted to a few dozen weekly means. A result that does not reject is not evidence that the trends were parallel, and the assumption itself concerns what would have happened after the campaign began, which no test can check.',
  },
};

/** @param {string} key */
export function metric(key) {
  const m = METRICS[key];
  if (!m) throw new Error(`unknown metric: ${key}`);
  return m;
}
