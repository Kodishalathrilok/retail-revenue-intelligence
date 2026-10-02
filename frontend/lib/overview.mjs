/**
 * Overview logic: which weeks to point at, how to word what the detector and
 * the campaign estimate say, and where each step of the workflow leads.
 *
 * Nothing here computes a new metric. Every figure is a value an endpoint
 * returned; these functions only pick rows (the highest week, the latest full
 * week), sort flagged weeks into "during enrolment" and "after", and turn the
 * numbers into a sentence.
 */

/**
 * Weeks before this are households still joining the panel, not demand. It is
 * the forecasting contract's HISTORY_FLOOR_WEEK (src/rrip/forecast/contract.py);
 * tests/test_overview_product.py fails if the two drift apart.
 */
export const ENROLMENT_FLOOR_WEEK = 20;

/** Used when no department is chosen, or the chosen one has no forecast.
 *  The Forecast page opens on the same department. */
export const DEFAULT_FORECAST_DEPARTMENT = 'GROCERY';

/** The product in four steps. Each one is a real page or section. */
export const STEPS = [
  { key: 'detect', verb: 'Detect', question: 'What changed?',
    blurb: 'Weekly revenue, with unusual weeks flagged.', href: '/#what-changed' },
  { key: 'explain', verb: 'Explain', question: 'What is behind it?',
    blurb: 'Ask in plain English. PostgreSQL computes the answer.', href: '/query' },
  { key: 'predict', verb: 'Predict', question: 'What is likely next?',
    blurb: 'Next week’s revenue, with its measured error.', href: '/forecast' },
  { key: 'investigate', verb: 'Investigate', question: 'Could a campaign have caused it?',
    blurb: 'A campaign effect estimate, with its uncertainty.', href: '/causal' },
];

const usd = (v, dp = 0) => Number(v).toLocaleString('en-US', {
  style: 'currency', currency: 'USD', minimumFractionDigits: dp, maximumFractionDigits: dp,
});

/** "+$1.51" / "−$2.35": a signed dollar amount, with a real minus sign. */
export function signedMoney(v, dp = 2) {
  const n = Number(v);
  return `${n < 0 ? '−' : '+'}${usd(Math.abs(n), dp)}`;
}

const full = (weekly) => weekly.filter((w) => !w.is_partial_week);

/** The full week with the highest revenue, or null. */
export function peakWeek(weekly) {
  return full(weekly).reduce(
    (best, w) => (best === null || Number(w.revenue) > Number(best.revenue) ? w : best), null);
}

/** The most recent full (7-day) week, or null. */
export function latestFullWeek(weekly) {
  const weeks = full(weekly);
  return weeks.length ? weeks.reduce((a, b) => (b.week_no > a.week_no ? b : a)) : null;
}

/** Flagged weeks, sorted, split at the enrolment floor. */
export function splitFlagged(flagged, floor = ENROLMENT_FLOOR_WEEK) {
  const sorted = [...flagged].sort((a, b) => a.week_no - b.week_no);
  return {
    enrolment: sorted.filter((w) => w.week_no < floor),
    steady: sorted.filter((w) => w.week_no >= floor),
  };
}

/** "week 5", "weeks 1–7" or "weeks 1, 3 and 9". */
export function weekList(weeks) {
  const n = weeks.map((w) => w.week_no);
  if (n.length === 0) return '';
  if (n.length === 1) return `week ${n[0]}`;
  const contiguous = n.every((v, i) => i === 0 || v === n[i - 1] + 1);
  if (contiguous) return `weeks ${n[0]}–${n[n.length - 1]}`;
  return `weeks ${n.slice(0, -1).join(', ')} and ${n[n.length - 1]}`;
}

/**
 * What the detector's result means, in words. The detector flags the enrolment
 * ramp; saying only "7 anomalies" would repeat the mistake the chart used to
 * make, so the split is stated.
 * @returns {{count: number, headline: string, detail: string}}
 */
export function flaggedSummary(flagged, floor = ENROLMENT_FLOOR_WEEK) {
  const { enrolment, steady } = splitFlagged(flagged, floor);
  const count = flagged.length;
  const s = (k) => (k === 1 ? '' : 's');
  if (count === 0) {
    return { count, headline: 'No week is flagged',
             detail: 'No week’s revenue is far enough from the average to cross the threshold.' };
  }
  if (steady.length === 0) {
    return {
      count,
      headline: `${count} week${s(count)} flagged, all while households were still joining`,
      detail: `The flagged weeks are ${weekList(enrolment)}. That is the panel filling up, not a `
        + `change in demand. No week from ${floor} onward crosses the threshold.`,
    };
  }
  return {
    count,
    headline: `${steady.length} week${s(steady.length)} flagged after enrolment`,
    detail: `Flagged after the panel was full: ${weekList(steady)}.`
      + (enrolment.length
        ? ` Another ${enrolment.length} (${weekList(enrolment)}) fall in the enrolment period `
          + 'and reflect the panel filling up, not demand.'
        : ''),
  };
}

/**
 * The campaign estimate in one sentence. Never "no effect" and never "proven":
 * an interval that includes zero is stated as exactly that.
 * @returns {{includesZero: boolean, sentence: string}}
 */
export function effectSummary(a) {
  const includesZero = a.ci_low <= 0 && a.ci_high >= 0;
  const est = signedMoney(a.did_estimate);
  const lo = signedMoney(a.ci_low);
  const hi = signedMoney(a.ci_high);
  return {
    includesZero,
    sentence: includesZero
      ? `The estimate is ${est} per household per week, but the 95% interval runs from ${lo} `
        + `to ${hi}. It includes zero, so the data do not rule out a zero or small effect. `
        + 'That is not the same as showing there was no effect.'
      : `The estimate is ${est} per household per week, and the 95% interval (${lo} to ${hi}) `
        + 'excludes zero. It is still an estimate that rests on the parallel-trends assumption, '
        + 'not proof.',
  };
}

/**
 * How far to trust the campaign estimate, in plain words: the verdict that
 * leads the Causal page. It reads only what the API reported (the pre-trend
 * test, the evidence label, the interval) and never upgrades it.
 * @returns {{headline: string, detail: string}}
 */
export function campaignVerdict(a) {
  if (!a.parallel_trends.passed) {
    return {
      headline: 'This estimate should not be attributed to the campaign',
      detail: 'The two groups were already moving apart before the campaign started, so the '
        + 'comparison picks up that divergence as well as anything the campaign did.',
    };
  }
  if (a.confidence === 'NOT CREDIBLE') {
    return {
      headline: 'This estimate should not be attributed to the campaign',
      detail: 'The checks reported with it do not support reading it as the effect of this campaign.',
    };
  }
  if (effectSummary(a).includesZero) {
    return {
      headline: 'The data do not rule out a zero or small effect',
      detail: 'The interval spans both a decrease and an increase in weekly spend. That is not '
        + 'evidence that the campaign did nothing.',
    };
  }
  return {
    headline: `The data point to ${a.did_estimate < 0 ? 'a decrease' : 'an increase'} in weekly spend`,
    detail: 'The interval excludes zero. It is still an estimate that depends on the assumptions '
      + 'listed here, not proof.',
  };
}

/**
 * The pre-trend test's result in plain words. A pass is worded as
 * non-rejection, never as the trends being parallel.
 *
 * This is the only pre-trend sentence the pages show. The API also stores a
 * verdict string ("did not reject parallel trends"), which names the opposite
 * null from the approved sentence; shown side by side the two read as a
 * contradiction, so the stored text is never rendered. It is built from the
 * figures alone: whether the test passed and its p-value.
 */
export function pretrendSentence(pt) {
  const p = Number(pt.interaction_pvalue);
  if (!Number.isFinite(p)) {
    return 'There were too few pre-campaign weeks to run the test, so the parallel-trends '
      + 'assumption is unchecked.';
  }
  return pt.passed
    ? `The test did not reject differential pre-treatment trends (interaction p = ${p.toFixed(3)}). `
      + 'It found no sign of the groups diverging before the campaign, but it is a low-power test '
      + 'and cannot confirm that they were moving in parallel.'
    : `The test found the two groups already diverging before the campaign (interaction p = ${p.toFixed(4)}), `
      + 'so the parallel-trends assumption is violated.';
}

/**
 * A stored analysis warning, as the page shows it. The warnings were written
 * when the estimate sat under them; it now leads the page, so "the estimate
 * below" points the wrong way. Only that phrase is changed.
 */
export function displayWarning(warning) {
  return String(warning).replace(/\bthe estimate below\b/g, 'this estimate');
}

/**
 * The challenger's forecast beside the live one, for the Forecast page.
 *
 * Both figures are passed through exactly as the payload gave them. The only
 * arithmetic is the difference: challenger minus live, taken between the two
 * amounts in whole dollars (which is how the page prints them, so the three
 * numbers reconcile on screen), and that difference as a percentage of the
 * live forecast.
 *
 * Null when there is nothing honest to show: no challenger figure, or a
 * payload that says the challenger is the deployed predictor, in which case
 * "not deployed" would be false.
 * @returns {{live: number, challenger: number, difference: number,
 *            percent: number|null, differenceText: string}|null}
 */
export function challengerComparison(forecast) {
  const c = forecast?.challenger_model;
  if (!c || c.deployed || c.prediction === null || c.prediction === undefined) return null;
  const live = Number(forecast.prediction);
  const challenger = Number(c.prediction);
  if (!Number.isFinite(live) || !Number.isFinite(challenger)) return null;
  const difference = Math.round(challenger) - Math.round(live);
  const percent = Math.round(live) === 0 ? null : (100 * difference) / Math.round(live);
  const pct = percent === null ? ''
    : ` (${percent < 0 ? '−' : '+'}${Math.abs(percent).toFixed(1)}%)`;
  return { live, challenger, difference, percent,
           differenceText: `${signedMoney(difference, 0)}${pct}` };
}

/** A link into Ask with the question filled in. Ask never auto-runs it. */
export function askHref(question) {
  return `/query?q=${encodeURIComponent(question)}`;
}

/** The "explore drivers" question for one week. Verified against the
 *  published tier: it reads pub_weekly_revenue_by_dept. */
export function driversQuestion(weekNo) {
  return `Which 5 departments had the highest revenue in week ${weekNo}?`;
}

/** The department whose forecast the Overview shows. */
export function forecastDepartment(chosen, servable) {
  return chosen && servable.includes(chosen) ? chosen : DEFAULT_FORECAST_DEPARTMENT;
}

/**
 * Text alternative for the weekly revenue chart.
 * @param {{week_no: number, revenue: string|number, is_partial_week: boolean}[]} weekly
 * @param {{department?: string, floor?: number, flaggedWeeks?: number[]}} [opts]
 */
export function weeklyAlt(weekly, { department = '', floor = ENROLMENT_FLOOR_WEEK,
                                    flaggedWeeks = [] } = {}) {
  if (weekly.length === 0) return 'Weekly revenue chart: no data.';
  const first = weekly[0].week_no;
  const last = weekly[weekly.length - 1].week_no;
  const peak = peakWeek(weekly);
  const latest = latestFullWeek(weekly);
  const parts = [
    `Line chart of weekly revenue for ${department || 'all departments'}, weeks ${first} to ${last}.`,
  ];
  if (first < floor) {
    parts.push(`Weeks before ${floor} are shaded: households were still joining the panel, `
      + 'so the rise there is enrolment, not demand.');
  }
  if (peak) parts.push(`The highest full week is week ${peak.week_no} at ${usd(peak.revenue)}.`);
  if (latest) parts.push(`The latest full week is week ${latest.week_no} at ${usd(latest.revenue)}.`);
  if (flaggedWeeks.length) parts.push(`Flagged weeks: ${flaggedWeeks.join(', ')}.`);
  return parts.join(' ');
}
