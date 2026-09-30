/**
 * Deterministic answer-building for the Ask page.
 *
 * Pure functions over the /api/v1/ai/query response. Every figure these produce
 * is read from the result rows the database returned; no language model writes
 * any of this text. That is the product's rule -- the model proposes SQL,
 * Postgres computes, and this module only formats and explains what came back.
 *
 * Plain JavaScript (.mjs, typed with JSDoc) so `node --test` can exercise it
 * with no build step; tests/test_frontend_logic.py runs lib/answer.test.mjs.
 */

// Columns whose values are money. Checked after PERCENT so "revenue_share" and
// "pct_of_revenue" are not formatted as dollars.
const MONEY = /(revenue|sales|spend|value|amount)/i;
// Columns whose values are ALREADY in percent units in this schema
// (pct_of_revenue = 45.6 means 45.6%). "share" alone is deliberately absent:
// it could be a 0..1 fraction, and guessing a scale would misstate the figure.
const PERCENT = /(^|_)(pct|percent)(_|$)|_rate$|(^|_)rate(_|$)/i;
// Never money even when the name says "revenue": revenue_share = 0.456 was
// formatted as "$0" before this existed.
const NOT_MONEY = /(share|ratio|lift|count|rank)/i;

const KNOWN_LABELS = {
  pct_of_revenue: 'share of revenue',
  pct_of_panel: 'share of households',
  avg_household_reorder_rate: 'average household reorder rate',
  avg_line_reorder_rate: 'average line reorder rate',
  total_revenue: 'total revenue',
  segment_revenue: 'segment revenue',
  week_no: 'week',
};

/** @param {unknown} v */
export function isNumeric(v) {
  if (typeof v === 'number') return Number.isFinite(v);
  return typeof v === 'string' && /^-?\d+(\.\d+)?$/.test(v.trim());
}

/** @param {string} col */
export function humanize(col) {
  if (KNOWN_LABELS[col]) return KNOWN_LABELS[col];
  return col
    .replace(/_/g, ' ')
    .replace(/\bavg\b/gi, 'average')
    .replace(/\bpct\b/gi, 'percent')
    .trim();
}

const cap = (s) => (s ? s[0].toUpperCase() + s.slice(1) : s);

/** @param {string} col @param {unknown} v */
export function formatValue(col, v) {
  if (v === null || v === undefined) return '—';
  if (!isNumeric(v)) return String(v);
  const n = Number(v);
  if (PERCENT.test(col)) {
    return `${n.toLocaleString('en-US', { maximumFractionDigits: 1 })}%`;
  }
  if (MONEY.test(col) && !NOT_MONEY.test(col)) {
    return n.toLocaleString('en-US', { style: 'currency', currency: 'USD',
                                       maximumFractionDigits: 0 });
  }
  return n.toLocaleString('en-US', { maximumFractionDigits: Number.isInteger(n) ? 0 : 3 });
}

/** Columns whose every value is numeric / is a non-numeric string. */
function columnKinds(columns, rows) {
  const numeric = columns.filter((c) => rows.every((r) => isNumeric(r[c])));
  const text = columns.filter((c) => rows.every((r) => typeof r[c] === 'string' && !isNumeric(r[c])));
  return { numeric, text };
}

/**
 * A one-row, one-column result holding a long sentence is the model declining
 * (the published-tier prompt tells it to answer out-of-scope questions with
 * SELECT '...' AS message). It must read as "not answerable", never as data.
 */
export function isProseRefusal(result) {
  if (!result?.succeeded || result.rows?.length !== 1 || result.columns?.length !== 1) return false;
  const only = result.rows[0][result.columns[0]];
  return typeof only === 'string' && !isNumeric(only) && only.trim().length > 20;
}

/**
 * The business-readable answer. Built only from the rows; it claims a ranking
 * ("ranks first") only when the rows are actually in descending order of the
 * first numeric column, and otherwise just points at the table.
 */
export function answerSentence(result) {
  const rows = result?.rows ?? [];
  const columns = result?.columns ?? [];
  if (rows.length === 0) return 'The query ran and returned no rows.';
  const { numeric, text } = columnKinds(columns, rows);

  if (rows.length === 1 && columns.length === 1 && numeric.length === 1) {
    return `${cap(humanize(columns[0]))}: ${formatValue(columns[0], rows[0][columns[0]])}.`;
  }
  const label = (r) => text.map((c) => r[c]).join(' + ');
  if (rows.length === 1 && text.length >= 1 && numeric.length >= 1) {
    const figures = numeric.map((c) => `${humanize(c)} ${formatValue(c, rows[0][c])}`).join(', ');
    return `${label(rows[0])}: ${figures}.`;
  }
  if (rows.length > 1 && text.length >= 1 && numeric.length >= 1) {
    const primary = numeric[0];
    const values = rows.map((r) => Number(r[primary]));
    const descending = values.every((v, i) => i === 0 || v <= values[i - 1]);
    if (descending) {
      return `${label(rows[0])} ranks first by ${humanize(primary)} ` +
             `(${formatValue(primary, rows[0][primary])}), of ${rows.length} results.`;
    }
    return `${rows.length} results — see the table below.`;
  }
  return `${rows.length} row${rows.length === 1 ? '' : 's'} returned — see the table below.`;
}

/**
 * What kind of outcome this is, so the page can say WHY a question was not
 * answered. Uses the router's verdict when the API sends it; falls back to
 * the failure reason text otherwise (older API revisions).
 *
 * @returns {{kind: string, title: string, message: string|null, clarification: string|null}}
 */
export function outcome(result) {
  if (result?.succeeded) {
    if (isProseRefusal(result)) {
      return { kind: 'unanswerable', title: 'Not answerable from this dataset',
               message: String(result.rows[0][result.columns[0]]), clarification: null };
    }
    return { kind: 'answer', title: 'Answer', message: null, clarification: null };
  }
  const r = result?.routing ?? null;
  // The router's reason only explains a refusal. When it said ANSWERABLE and the
  // failure came later (a check rejected every draft, or the provider was
  // down), its reason is an internal note ("no missing data or unresolved
  // choice detected") -- say what actually happened instead.
  const refusedByRouter = r?.verdict && r.verdict !== 'ANSWERABLE';
  const message = refusedByRouter
    ? (r.reason ?? result?.failure_reason ?? null)
    : (result?.failure_reason ?? 'The drafted query did not pass the safety checks, so nothing was run.');
  const clarification = refusedByRouter ? (r.clarification ?? null) : null;
  const TITLES = {
    UNSAFE: ['refused', 'This system is read-only'],
    UNSUPPORTED: ['unsupported', 'That is not in this dataset'],
    AMBIGUOUS: ['ambiguous', 'That question could mean more than one thing'],
    TOO_EXPENSIVE: ['too_broad', 'That is too broad to run safely'],
    FORECAST: ['forecast', 'That is a question about the future'],
  };
  const [kind, title] = TITLES[r?.verdict] ?? ['failed', 'No safe query could be produced'];
  return { kind, title, message, clarification };
}

/** Tables the SQL reads, as written in it. Only real table-name prefixes. */
export function referencedTables(sql) {
  if (!sql) return [];
  const found = sql.match(/\b(pub|fact|dim|bridge)_[a-z0-9_]+\b/gi) ?? [];
  return [...new Set(found.map((t) => t.toLowerCase()))];
}

/**
 * A chart only where the result's shape supports one without implying a
 * meaning it does not have: a line over weeks, or bars for a short labelled
 * ranking. Anything else gets the table alone.
 *
 * @returns {null | {type: 'line'|'bar', x: string, y: string, labels?: string[]}}
 */
export function chartSpec(result) {
  const rows = result?.rows ?? [];
  const columns = result?.columns ?? [];
  if (isProseRefusal(result) || rows.length < 2) return null;
  const { numeric, text } = columnKinds(columns, rows);
  const week = columns.find((c) => /^week(_no)?$/i.test(c));
  if (week && numeric.includes(week) && rows.length >= 5) {
    const y = numeric.find((c) => c !== week);
    return y ? { type: 'line', x: week, y } : null;
  }
  if (text.length >= 1 && numeric.length >= 1 && rows.length <= 20) {
    return { type: 'bar', x: text.join('+'), labels: text, y: numeric[0] };
  }
  return null;
}

/**
 * The "Why should I trust this?" content, from what the backend actually
 * returned -- nothing here is asserted without a source in the response or in
 * /health.
 */
export function evidence(result, health, shownRows) {
  const sql = result?.sql ?? '';
  const last = result?.attempts?.[result.attempts.length - 1];
  const limitations = [];
  if (health?.tier === 'published') {
    limitations.push('This demo reads pre-computed aggregate tables (pub_*). Questions ' +
      'about individual baskets, households or product-level promotions cannot be answered here.');
  }
  if ((result?.row_count ?? 0) > shownRows) {
    limitations.push(`Showing ${shownRows} of ${result.row_count} rows.`);
  }
  if (/\bweek/i.test(sql)) {
    limitations.push('Weeks 1 and 102 are partial (5 and 6 days) and not comparable to a full week.');
  }
  limitations.push('The language model wrote the query; PostgreSQL computed every figure from it.');
  return {
    source: health ? { tier: health.tier, tables: health.published_tables ?? null,
                       publishedAt: health.published_at ?? null } : null,
    tables: referencedTables(sql),
    sql: sql || null,
    checks: (last?.stages ?? []).map((s) => ({ stage: s.stage, passed: s.passed, detail: s.detail })),
    attempts: result?.attempts?.length ?? 0,
    revenueBasis: /revenue|sales_value/i.test(sql),
    limitations,
  };
}
