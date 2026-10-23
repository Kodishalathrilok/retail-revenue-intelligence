import assert from 'node:assert/strict';
import { test } from 'node:test';
import { retentionAt, retentionSeries, topSegment } from './customers.mjs';

const RETENTION = [
  { segment: 'Largest first basket', tenure_month: 0, retention_pct: 100 },
  { segment: 'Largest first basket', tenure_month: 12, retention_pct: 82.5 },
  { segment: 'Smallest first basket', tenure_month: 0, retention_pct: 100 },
  { segment: 'Smallest first basket', tenure_month: 12, retention_pct: 78.7 },
  { segment: 'Middle', tenure_month: 0, retention_pct: 100 },
  { segment: 'Middle', tenure_month: 12, retention_pct: 81.9 },
];

test('retentionSeries pivots to one row per month with a column per group', () => {
  const { data, segments } = retentionSeries(RETENTION);
  // Groups ordered low -> high regardless of input order.
  assert.deepEqual(segments, ['Smallest first basket', 'Middle', 'Largest first basket']);
  // One row per distinct tenure month, ascending.
  assert.deepEqual(data.map((r) => r.tenure_month), [0, 12]);
  const m12 = data.find((r) => r.tenure_month === 12);
  assert.equal(m12['Largest first basket'], 82.5);
  assert.equal(m12['Smallest first basket'], 78.7);
});

test('retentionSeries fills a missing month for a group with null, not a guess', () => {
  const sparse = [
    { segment: 'Middle', tenure_month: 0, retention_pct: 100 },
    { segment: 'Middle', tenure_month: 6, retention_pct: 90 },
    { segment: 'Largest first basket', tenure_month: 0, retention_pct: 100 },
  ];
  const { data } = retentionSeries(sparse);
  const m6 = data.find((r) => r.tenure_month === 6);
  assert.equal(m6['Largest first basket'], null);
});

test('retentionSeries ignores non-numeric rows', () => {
  const { data } = retentionSeries([{ segment: 'Middle', tenure_month: 'x', retention_pct: 'y' }]);
  assert.equal(data.length, 0);
});

test('retentionAt returns each group at a month, ordered low -> high', () => {
  const at = retentionAt(RETENTION, 12);
  assert.deepEqual(at.map((x) => x.segment),
    ['Smallest first basket', 'Middle', 'Largest first basket']);
  assert.deepEqual(at.map((x) => x.pct), [78.7, 81.9, 82.5]);
});

test('topSegment finds the largest share of revenue and its concentration', () => {
  const segs = [
    { segment: 'Champions', pct_of_panel: 20.5, pct_of_revenue: 45.6 },
    { segment: 'Loyal', pct_of_panel: 25.7, pct_of_revenue: 27.4 },
  ];
  assert.deepEqual(topSegment(segs), { segment: 'Champions', pctHouseholds: 20.5, pctRevenue: 45.6 });
});

test('topSegment returns null on empty input', () => {
  assert.equal(topSegment([]), null);
  assert.equal(topSegment(undefined), null);
});
