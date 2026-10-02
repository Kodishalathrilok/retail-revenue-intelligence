// node --test frontend/lib/overview.test.mjs   (run by tests/test_frontend_logic.py)
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  DEFAULT_FORECAST_DEPARTMENT, DRIVERS_QUESTION_NOTE, ENROLMENT_FLOOR_WEEK, STEPS, askHref,
  campaignVerdict,
  challengerComparison, displayWarning, driversQuestion, effectSummary, flaggedSummary,
  forecastDepartment, latestFullWeek, peakWeek, pretrendSentence, signedMoney, splitFlagged,
  weekList, weeklyAlt,
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

// What production returns for campaign 26 (clean) and campaign 18
// (contaminated), stored verdict and warning strings included, verbatim.
const C26 = { did_estimate: 1.5068, ci_low: -2.3494, ci_high: 5.363, confidence: 'WEAK',
  parallel_trends: { passed: true, interaction_pvalue: 0.387544,
    verdict: 'The pre-period test did not reject parallel trends (interaction p = 0.388). This is '
      + 'a low-power linear test, so it is consistent with parallel trends but does not prove them.' },
  warnings: ['the DiD estimate is not statistically distinguishable from zero (p = 0.444).'] };
const C18 = { did_estimate: -5.3455, ci_low: -11.9133, ci_high: 1.2224, confidence: 'NOT CREDIBLE',
  parallel_trends: { passed: false, interaction_pvalue: 0.011849,
    verdict: 'Pre-period trends DIVERGE (interaction p = 0.0118). Parallel trends is VIOLATED and '
      + 'the DiD estimate is not attributable to the campaign.' },
  warnings: [
    'PARALLEL TRENDS VIOLATED -- the groups were already diverging before the campaign, so the '
      + 'estimate below cannot be attributed to it.',
    '90.8% of enrolled households were simultaneously in an overlapping campaign; this measures '
      + 'a bundle, not this campaign.',
    'the DiD estimate is not statistically distinguishable from zero (p = 0.111).'] };

/** Every string the Causal page and its Explain panel build for one campaign. */
const rendered = (a) => [
  campaignVerdict(a).headline, campaignVerdict(a).detail, effectSummary(a).sentence,
  pretrendSentence(a.parallel_trends), ...a.warnings.map(displayWarning),
];

// Phrasings the project rules out, wherever they would be shown.
const RULED_OUT = [
  /parallel[- ]trends?\s+(hold|holds|held|are proven|is proven|proven|confirmed)/i,
  /trends\s+(hold|are parallel)\b/i,
  /did not reject parallel trends/i,           // the stored verdict's wording
  /\bthe estimate below\b/i,                   // the estimate now leads the page
  /\b(there (is|was)|shows?|showed|found|means|had) no effect\b/i,
  /\b(four|4)[- ]weeks?[- ]ahead\b|\bnext (four|4) weeks\b|\b(four|4)-week forecasts?\b/i,
];
// "does not show there was no effect" is the caveat itself, not the claim.
const claims = (text) => text
  .replace(/(does not|do not|not the same as) show(ing)?\s+there was no effect/gi, '');

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
  // Too few pre-campaign weeks: no p-value. The page still words it itself.
  const untested = pretrendSentence({ passed: false, interaction_pvalue: NaN, verdict: 'INSUFFICIENT' });
  assert.match(untested, /too few pre-campaign weeks/);
  assert.doesNotMatch(untested, /INSUFFICIENT/);
});

test('the stored pre-trend verdict is never what the page shows', () => {
  for (const a of [C26, C18]) {
    const shown = pretrendSentence(a.parallel_trends);
    assert.notEqual(shown, a.parallel_trends.verdict);
    assert.ok(!rendered(a).includes(a.parallel_trends.verdict));
  }
  // The approved sentence and the stored one name opposite nulls; only one may appear.
  assert.match(pretrendSentence(C26.parallel_trends), /did not reject differential pre-treatment trends/);
  assert.doesNotMatch(rendered(C26).join(' '), /did not reject parallel trends/);
});

test('a stored warning that points at "the estimate below" is reworded, and only that', () => {
  assert.equal(displayWarning(C18.warnings[0]),
    'PARALLEL TRENDS VIOLATED -- the groups were already diverging before the campaign, so '
    + 'this estimate cannot be attributed to it.');
  assert.equal(displayWarning(C18.warnings[1]), C18.warnings[1]);
  assert.equal(displayWarning(C26.warnings[0]), C26.warnings[0]);
});

test('nothing rendered for either campaign uses a ruled-out phrasing', () => {
  for (const a of [C26, C18]) {
    for (const text of rendered(a)) {
      for (const pattern of RULED_OUT) assert.doesNotMatch(claims(text), pattern);
    }
  }
  // The scan has teeth: the stored strings themselves trip it.
  assert.ok(RULED_OUT.some((p) => p.test(C26.parallel_trends.verdict)));
  assert.ok(RULED_OUT.some((p) => p.test(C18.warnings[0])));
});

test('no Explain text uses a ruled-out phrasing', () => {
  for (const [key, m] of Object.entries(METRICS)) {
    const text = claims([m.label, m.definition, m.source, m.calculation, m.limitation].join(' '));
    for (const pattern of RULED_OUT) assert.doesNotMatch(text, pattern, key);
  }
});

// The forecast payload production returns for GROCERY, week 102 (the fields used).
const GROCERY = { prediction: 49031.5,
  challenger_model: { name: 'hgb(lr=0.02,leaves=7,min_leaf=40)', prediction: 48194.26, deployed: false } };

test('the challenger figure shown is the payload value, untouched', () => {
  const c = challengerComparison(GROCERY);
  assert.equal(c.challenger, GROCERY.challenger_model.prediction);
  assert.equal(c.live, GROCERY.prediction);
});

test('the difference is challenger minus live, and reconciles with the two figures as printed', () => {
  const c = challengerComparison(GROCERY);
  // Printed in whole dollars: $49,032 live and $48,194 challenger.
  assert.equal(c.difference, 48194 - 49032);
  assert.equal(c.differenceText, '−$838 (−1.7%)');
  const higher = challengerComparison({ prediction: 100, challenger_model: { prediction: 227.6, deployed: false } });
  assert.equal(higher.differenceText, '+$128 (+128.0%)');
  const zero = challengerComparison({ prediction: 0, challenger_model: { prediction: 5, deployed: false } });
  assert.equal(zero.percent, null);
  assert.equal(zero.differenceText, '+$5');
});

test('no comparison is offered when it would be untrue or empty', () => {
  // A deployed challenger is not "not deployed".
  assert.equal(challengerComparison({ ...GROCERY,
    challenger_model: { ...GROCERY.challenger_model, deployed: true } }), null);
  assert.equal(challengerComparison({ prediction: 5, challenger_model: { prediction: null, deployed: false } }), null);
  assert.equal(challengerComparison({ prediction: 5 }), null);
  assert.equal(challengerComparison(null), null);
});

// Words that would say one method is the more accurate. Allowed only inside a
// sentence that negates them ("did not beat", "was not more accurate").
const COMPARATIVE = /\b(better|improved?|improves|improvement|outperform\w*|superior|beats?|more accurate)\b/i;
const NEGATED = /\b(not|no|never|cannot|neither|nor)\b|n’t|n't/i;

test('no forecast Explain text says the model is better, improved or outperforms', () => {
  for (const [key, m] of Object.entries(METRICS)) {
    if (!key.startsWith('forecast')) continue;
    const text = [m.label, m.definition, m.source, m.calculation, m.limitation].join(' ');
    for (const sentence of text.split(/(?<=[.!?])\s+/)) {
      if (COMPARATIVE.test(sentence)) assert.match(sentence, NEGATED, `${key}: ${sentence}`);
    }
  }
  // The scan has teeth.
  assert.ok(COMPARATIVE.test('The model outperforms the average.'));
  assert.ok(!NEGATED.test('The model outperforms the average.'));
});

test('the challenger Explain entry says what it is and what it is not', () => {
  const m = METRICS.forecast_challenger;
  assert.match(m.definition, /tested, and not deployed/);
  assert.match(m.limitation, /not the live forecast/);
  assert.match(m.limitation, /not more accurate than the live method/);
  assert.match(m.limitation, /range on this page belongs to the live forecast/);
});

test('ask links carry the question and the drivers question names the week', () => {
  assert.equal(driversQuestion(92), 'Which 5 departments had the highest revenue in week 92?');
  // The question is pre-filled, never auto-run, and labelled where it is offered.
  assert.equal(DRIVERS_QUESTION_NOTE, 'Not a verified demo question.');
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
