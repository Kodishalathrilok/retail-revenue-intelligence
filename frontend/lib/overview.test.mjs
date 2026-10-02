// node --test frontend/lib/overview.test.mjs   (run by tests/test_frontend_logic.py)
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  DEFAULT_FORECAST_DEPARTMENT, ENROLMENT_FLOOR_WEEK, STEPS, askHref, campaignVerdict,
  driversQuestion, effectSummary, flaggedSummary, forecastDepartment, latestFullWeek, peakWeek,
  pretrendSentence, signedMoney, splitFlagged, weekList, weeklyAlt,
} from './overview.mjs';
import { METRICS, metric } from './metrics.mjs';

const wk = (week_no, revenue, is_partial_week = false) => ({ week_no, revenue: String(revenue), is_partial_week });

// The shape production returns: a ramp, a peak at 92, a partial last week.
const WEEKLY = [wk(1, 5211.16, true), wk(2, 10821.35), wk(20, 86109), wk(92, 113193.02),
  wk(101, 84407.25), wk(102, 171968.63, true)];
// What /ai/anomalies returns today: seven weeks, all during enrolment.
const FLAGGED = [1, 2, 3, 4, 5, 6, 7].map((n) => ({ week_no: n, revenue: '1', z_score: '-3' }));

test('the peak and the latest week skip partial weeks', () => {
  assert.equal(peakWeek(WEEKLY).week_no, 92);       // not partial week 102, though it is larger
  assert.equal(latestFullWeek(WEEKLY).week_no, 101);
  assert.equal(peakWeek([]), null);
  assert.equal(latestFullWeek([wk(1, 5, true)]), null);
});

test('flagged weeks are split at the enrolment floor', () => {
  const { enrolment, steady } = splitFlagged([...FLAGGED, { week_no: 20 }, { week_no: 92 }]);
  assert.deepEqual(enrolment.map((w) => w.week_no), [1, 2, 3, 4, 5, 6, 7]);
  assert.deepEqual(steady.map((w) => w.week_no), [20, 92]);
  assert.equal(ENROLMENT_FLOOR_WEEK, 20);
});

test('flags that all fall in enrolment are not presented as a change in demand', () => {
  const s = flaggedSummary(FLAGGED);
  assert.equal(s.count, 7);
  assert.match(s.headline, /all while households were still joining/);
  assert.match(s.detail, /weeks 1–7/);
  assert.match(s.detail, /not a change in demand/);
  assert.match(s.detail, /No week from 20 onward/);
});

test('a flag after enrolment leads, and the enrolment flags are still explained', () => {
  const s = flaggedSummary([...FLAGGED, { week_no: 92 }]);
  assert.equal(s.headline, '1 week flagged after enrolment');
  assert.match(s.detail, /week 92/);
  assert.match(s.detail, /Another 7 \(weeks 1–7\)/);
  assert.equal(flaggedSummary([]).headline, 'No week is flagged');
});

test('week lists read naturally', () => {
  assert.equal(weekList([{ week_no: 5 }]), 'week 5');
  assert.equal(weekList([{ week_no: 1 }, { week_no: 2 }, { week_no: 3 }]), 'weeks 1–3');
  assert.equal(weekList([{ week_no: 1 }, { week_no: 3 }, { week_no: 9 }]), 'weeks 1, 3 and 9');
});

test('signed money uses a real minus sign and two decimals', () => {
  assert.equal(signedMoney(1.5068), '+$1.51');
  assert.equal(signedMoney(-2.3494), '−$2.35');
  assert.equal(signedMoney(0), '+$0.00');
});

test('an interval that includes zero is never worded as no effect or as proof', () => {
  const s = effectSummary({ did_estimate: 1.5068, ci_low: -2.3494, ci_high: 5.363 });
  assert.equal(s.includesZero, true);
  assert.match(s.sentence, /\+\$1\.51 per household per week/);
  assert.match(s.sentence, /−\$2\.35 to \+\$5\.36/);
  assert.match(s.sentence, /do not rule out a zero or small effect/);
  assert.match(s.sentence, /not the same as showing there was no effect/);
  assert.doesNotMatch(s.sentence, /proves|proven|caused/);
});

test('an interval that excludes zero is still an estimate, not proof', () => {
  const s = effectSummary({ did_estimate: 4, ci_low: 1, ci_high: 7 });
  assert.equal(s.includesZero, false);
  assert.match(s.sentence, /excludes zero/);
  assert.match(s.sentence, /not proof/);
});

// What production returns for campaign 26 (clean) and campaign 18 (contaminated).
const C26 = { did_estimate: 1.5068, ci_low: -2.3494, ci_high: 5.363, confidence: 'WEAK',
  parallel_trends: { passed: true, interaction_pvalue: 0.387544, verdict: 'v' } };
const C18 = { did_estimate: -5.3455, ci_low: -11.9133, ci_high: 1.2224, confidence: 'NOT CREDIBLE',
  parallel_trends: { passed: false, interaction_pvalue: 0.011849, verdict: 'v' } };

test('the verdict for an interval that includes zero does not rule out a small effect', () => {
  const v = campaignVerdict(C26);
  assert.equal(v.headline, 'The data do not rule out a zero or small effect');
  assert.match(v.detail, /not evidence that the campaign did nothing/);
  assert.doesNotMatch(v.headline, /no effect|did not work|ineffective/i);
});

test('a failed pre-trend test or a not-credible label blocks attribution', () => {
  assert.match(campaignVerdict(C18).headline, /should not be attributed to the campaign/);
  assert.match(campaignVerdict(C18).detail, /already moving apart before the campaign/);
  const contaminated = { ...C26, confidence: 'NOT CREDIBLE' };
  assert.match(campaignVerdict(contaminated).headline, /should not be attributed/);
});

test('an interval that excludes zero is a direction, never proof', () => {
  const v = campaignVerdict({ ...C26, did_estimate: 4, ci_low: 1, ci_high: 7, confidence: 'CREDIBLE' });
  assert.match(v.headline, /point to an increase/);
  assert.match(v.detail, /not proof/);
  assert.match(campaignVerdict({ ...C26, did_estimate: -4, ci_low: -7, ci_high: -1 }).headline, /a decrease/);
});

test('a pre-trend pass is worded as non-rejection, never as parallel trends holding', () => {
  const s = pretrendSentence(C26.parallel_trends);
  assert.match(s, /^The test did not reject differential pre-treatment trends \(interaction p = 0\.388\)\./);
  assert.match(s, /low-power test/);
  assert.match(s, /cannot confirm/);
  assert.doesNotMatch(s, /trends hold|holds|proven|proves|supported|confirmed/i);
  const failed = pretrendSentence(C18.parallel_trends);
  assert.match(failed, /already diverging before the campaign \(interaction p = 0\.0118\)/);
  assert.match(failed, /violated/);
  // Too few pre-campaign weeks: no p-value, so the API's wording stands.
  assert.equal(pretrendSentence({ passed: false, interaction_pvalue: NaN, verdict: 'INSUFFICIENT' }), 'INSUFFICIENT');
});

test('ask links carry the question and the drivers question names the week', () => {
  assert.equal(driversQuestion(92), 'Which 5 departments had the highest revenue in week 92?');
  assert.equal(askHref('a b?'), '/query?q=a%20b%3F');
});

test('the forecast falls back to the default department when the chosen one is not served', () => {
  assert.equal(forecastDepartment('MEAT', ['GROCERY', 'MEAT']), 'MEAT');
  assert.equal(forecastDepartment('GARDEN CENTER', ['GROCERY', 'MEAT']), DEFAULT_FORECAST_DEPARTMENT);
  assert.equal(forecastDepartment('', ['GROCERY']), DEFAULT_FORECAST_DEPARTMENT);
});

test('the chart text alternative says the early rise is enrolment', () => {
  const alt = weeklyAlt(WEEKLY, { flaggedWeeks: [1, 2] });
  assert.match(alt, /all departments, weeks 1 to 102/);
  assert.match(alt, /Weeks before 20 are shaded/);
  assert.match(alt, /enrolment, not demand/);
  assert.match(alt, /highest full week is week 92 at \$113,193/);
  assert.match(alt, /Flagged weeks: 1, 2\./);
  assert.doesNotMatch(alt, /growth|grew/);
  // A series that starts after the floor has nothing to shade.
  assert.doesNotMatch(weeklyAlt([wk(30, 5), wk(31, 6)]), /shaded/);
  assert.equal(weeklyAlt([]), 'Weekly revenue chart: no data.');
});

test('the workflow is four steps, in order, each with a real destination', () => {
  assert.deepEqual(STEPS.map((s) => s.verb), ['Detect', 'Explain', 'Predict', 'Investigate']);
  assert.deepEqual(STEPS.map((s) => s.href), ['/#what-changed', '/query', '/forecast', '/causal']);
  for (const s of STEPS) assert.ok(s.question.endsWith('?') && s.blurb.length > 10);
});

test('every Explain entry is complete', () => {
  for (const [key, m] of Object.entries(METRICS)) {
    for (const field of ['label', 'definition', 'source', 'endpoint', 'calculation', 'limitation']) {
      assert.ok(typeof m[field] === 'string' && m[field].length > 3, `${key}.${field}`);
    }
    assert.ok(m.endpoint.startsWith('/api/v1/'), `${key}.endpoint`);
    assert.ok(Array.isArray(m.tables) && m.tables.length > 0, `${key}.tables`);
    assert.doesNotMatch(m.limitation, /proves|proven/i, `${key} overclaims`);
  }
  assert.throws(() => metric('nope'), /unknown metric/);
});

test('the Explain text states the limits that matter', () => {
  assert.match(METRICS.weekly_revenue.limitation, /not rising demand/);
  assert.match(METRICS.flagged_weeks.limitation, /not a judgement about cause/);
  assert.match(METRICS.forecast.limitation, /One week ahead only/);
  assert.match(METRICS.campaign_effect.limitation, /not randomised/);
  assert.match(METRICS.campaign_effect.limitation, /does not show there was no effect/);
  assert.match(METRICS.campaign_interval.limitation, /do not rule out a zero or small effect/);
  assert.match(METRICS.pretrend_test.limitation, /not evidence that the trends were parallel/);
  assert.match(METRICS.forecast_method.limitation, /not evidence that no model could do better/);
});

test('no Explain entry claims parallel trends hold or a forecast beyond one week', () => {
  for (const [key, m] of Object.entries(METRICS)) {
    const text = [m.definition, m.source, m.calculation, m.limitation].join(' ');
    assert.doesNotMatch(text, /parallel trends (hold|are proven|were proven)|trends hold/i, key);
    assert.doesNotMatch(text, /(four|4)[- ]weeks?[- ]ahead|next (four|4) weeks|(four|4)-week forecast/i, key);
  }
});
