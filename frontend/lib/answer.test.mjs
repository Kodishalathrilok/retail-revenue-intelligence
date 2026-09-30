// node --test frontend/lib/answer.test.mjs   (run by tests/test_frontend_logic.py)
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  answerSentence, chartSpec, evidence, formatValue, isProseRefusal, outcome, referencedTables,
} from './answer.mjs';

const ok = (columns, rows, extra = {}) => ({ succeeded: true, columns, rows, row_count: rows.length,
  sql: 'SELECT 1', attempts: [], ...extra });

// The four verified demo shapes (rows as production returned them) -----------------
const TOP_DEPTS = ok(['department', 'revenue'], [
  { department: 'GROCERY', revenue: 4093814.14 }, { department: 'DRUG GM', revenue: 1055358.03 },
  { department: 'KIOSK-GAS', revenue: 544222.28 }, { department: 'PRODUCE', revenue: 523150.6 },
  { department: 'MEAT', revenue: 511208.4 }]);
const SEGMENT = ok(['segment', 'segment_revenue', 'pct_of_revenue'],
  [{ segment: 'Champions', segment_revenue: 3676507.17, pct_of_revenue: 45.6 }]);
const PAIRS = ok(['commodity_a', 'commodity_b', 'lift'], [
  { commodity_a: 'CEREAL/BREAKFAST', commodity_b: 'FROZEN', lift: 24.256 },
  { commodity_a: 'SEAFOOD - MISC', commodity_b: 'SEAFOOD-FRESH', lift: 20.553 }]);
const REORDER = ok(['department', 'avg_household_reorder_rate'],
  [{ department: 'SALAD BAR', avg_household_reorder_rate: 38.7 }]);

test('answer sentences are built from the rows', () => {
  assert.equal(answerSentence(TOP_DEPTS), 'GROCERY ranks first by revenue ($4,093,814), of 5 results.');
  assert.equal(answerSentence(SEGMENT),
    'Champions: segment revenue $3,676,507, share of revenue 45.6%.');
  assert.equal(answerSentence(PAIRS), 'CEREAL/BREAKFAST + FROZEN ranks first by lift (24.256), of 2 results.');
  assert.equal(answerSentence(REORDER), 'SALAD BAR: average household reorder rate 38.7%.');
  assert.equal(answerSentence(ok(['total_revenue'], [{ total_revenue: '8057463.08' }])),
    'Total revenue: $8,057,463.');
});

test('a ranking is only claimed when the rows are actually in descending order', () => {
  const unordered = ok(['department', 'revenue'], [
    { department: 'MEAT', revenue: 5 }, { department: 'GROCERY', revenue: 9 }]);
  assert.equal(answerSentence(unordered), '2 results — see the table below.');
  assert.equal(answerSentence(ok(['a'], [])), 'The query ran and returned no rows.');
});

test('percent columns are not rescaled, and "share" alone is never guessed', () => {
  assert.equal(formatValue('pct_of_revenue', 45.6), '45.6%');
  assert.equal(formatValue('retention_pct', 12), '12%');
  assert.equal(formatValue('revenue_share', 0.456), '0.456');   // scale unknown: plain number
  assert.equal(formatValue('revenue', 1234.5), '$1,235');
  assert.equal(formatValue('department', null), '—');
});

test('outcome kinds follow the router verdict, with its reason and clarification', () => {
  const failed = (verdict) => ({ succeeded: false, rows: [], columns: [],
    routing: { verdict, reason: `r-${verdict}`, clarification: 'try revenue' } });
  assert.equal(outcome(TOP_DEPTS).kind, 'answer');
  for (const [v, kind] of [['UNSAFE', 'refused'], ['UNSUPPORTED', 'unsupported'],
    ['AMBIGUOUS', 'ambiguous'], ['TOO_EXPENSIVE', 'too_broad'], ['FORECAST', 'forecast']]) {
    const o = outcome(failed(v));
    assert.equal(o.kind, kind, v);
    assert.equal(o.message, `r-${v}`);
    assert.equal(o.clarification, 'try revenue');
  }
  // Gate rejection or provider failure: router said ANSWERABLE, no rows came back.
  // The router's internal note must never be shown as the explanation.
  const gated = outcome({ succeeded: false, failure_reason: 'rejected by explain',
    routing: { verdict: 'ANSWERABLE', reason: 'no missing data or unresolved choice detected',
               clarification: null } });
  assert.equal(gated.kind, 'failed');
  assert.equal(gated.message, 'rejected by explain');
  const bare = outcome({ succeeded: false, failure_reason: null,
    routing: { verdict: 'ANSWERABLE', reason: 'no missing data or unresolved choice detected' } });
  assert.match(bare.message, /did not pass the safety checks/);
  // Older API revisions send no routing at all.
  assert.equal(outcome({ succeeded: false, failure_reason: 'nope' }).message, 'nope');
});

test('a model that declines with a prose SELECT reads as unanswerable, not as data', () => {
  const prose = ok(['message'], [{ message: 'This deployment exposes aggregate tables only.' }]);
  assert.ok(isProseRefusal(prose));
  assert.equal(outcome(prose).kind, 'unanswerable');
  assert.equal(chartSpec(prose), null);
  assert.ok(!isProseRefusal(ok(['segment'], [{ segment: 'Champions' }])));
});

test('charts only for shapes that support one', () => {
  assert.deepEqual(chartSpec(TOP_DEPTS), { type: 'bar', x: 'department', labels: ['department'], y: 'revenue' });
  assert.equal(chartSpec(SEGMENT), null);                         // a single row is not a chart
  const weeks = ok(['week_no', 'revenue'], [1, 2, 3, 4, 5].map((w) => ({ week_no: w, revenue: w * 10 })));
  assert.equal(chartSpec(weeks).type, 'line');
  assert.equal(chartSpec(ok(['a', 'b'], [{ a: 1, b: 2 }, { a: 3, b: 4 }])), null);   // no label column
  const many = ok(['d', 'v'], Array.from({ length: 25 }, (_, i) => ({ d: `x${i}`, v: 25 - i })));
  assert.equal(chartSpec(many), null);                            // too many bars to read
});

test('evidence comes only from the response and /health', () => {
  const r = { ...TOP_DEPTS, row_count: 80,
    sql: 'SELECT department, sum(revenue) FROM pub_weekly_revenue_by_dept GROUP BY 1 ORDER BY 2 DESC',
    attempts: [{ stages: [{ stage: 'shape', passed: true, detail: 'one SELECT' },
                          { stage: 'explain', passed: true, detail: 'cost 12' }] }] };
  const e = evidence(r, { tier: 'published', published_tables: 23, published_at: '2026-09-29' }, 50);
  assert.deepEqual(e.tables, ['pub_weekly_revenue_by_dept']);
  assert.deepEqual(e.checks.map((c) => c.stage), ['shape', 'explain']);
  assert.equal(e.source.tables, 23);
  assert.ok(e.revenueBasis);
  assert.ok(e.limitations.some((l) => l.includes('Showing 50 of 80 rows')));
  assert.ok(e.limitations.some((l) => l.includes('aggregate tables')));
  assert.ok(e.limitations.some((l) => l.includes('PostgreSQL computed every figure')));
  // Without /health, no data-tier claim is made.
  assert.equal(evidence(r, null, 50).source, null);
  assert.ok(!evidence(r, null, 50).limitations.some((l) => l.includes('aggregate tables')));
});

test('referenced tables are exactly the table names in the SQL', () => {
  assert.deepEqual(referencedTables(
    'SELECT a.x FROM pub_rfm_segments a JOIN pub_dim_household h ON true WHERE a.pct_of_revenue > 1'),
    ['pub_rfm_segments', 'pub_dim_household']);
  assert.deepEqual(referencedTables(null), []);
});
