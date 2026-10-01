'use client';

/**
 * Ask-page building blocks. Everything shown is formatted from the API
 * response by lib/answer.mjs; nothing here computes or invents a figure.
 */

import { useEffect, useState } from 'react';
import {
  Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { AXIS_TICK, CHART, GRID_PROPS, TOOLTIP_STYLE } from '@/components/chart-theme';
import { RevenueCaveat } from '@/components/Caveats';
import { Disclosure, FactRow as Row } from '@/components/ui';
import { formatValue, humanize } from '@/lib/answer.mjs';

export type Stage = { stage: string; passed: boolean; detail: string; duration_ms: number | null };
export type Attempt = {
  attempt: number; sql: string; stages: Stage[];
  rejected_reason: string | null; error_fed_back: string | null;
};
export type QueryResult = {
  question: string; succeeded: boolean; sql: string | null;
  columns: string[]; rows: Record<string, unknown>[]; row_count: number;
  attempts: Attempt[]; total_duration_ms: number; provider: string | null;
  failure_reason: string | null;
  routing?: { verdict: string; reason: string; clarification: string | null } | null;
};
export type Health = { tier?: string; published_tables?: number; published_at?: string } | null;
type Evidence = {
  source: { tier: string; tables: number | null; publishedAt: string | null } | null;
  tables: string[]; sql: string | null;
  checks: { stage: string; passed: boolean; detail: string }[];
  attempts: number; revenueBasis: boolean; limitations: string[];
};
type ChartSpec = { type: 'line' | 'bar'; x: string; y: string; labels?: string[] } | null;

/** Plain-language names for the validation checks. */
export const CHECK_HELP: Record<string, string> = {
  shape: 'Exactly one statement, and it must be a read-only SELECT',
  keywords: 'No commands that change data or reach outside the dataset',
  functions: 'Only approved SQL functions',
  explain: 'Planned by PostgreSQL first; rejected if too costly to run',
  execute: 'Run under a time limit and a row cap',
  result_shape: 'The result has columns and was not truncated',
};

const PHASES = ['Interpreting the question', 'Querying the published data', 'Checking the result'];

/**
 * The wait, narrated. One request runs all three phases server-side, in this
 * order; the highlighted phase advances with elapsed time, so it is a guide to
 * what is happening, never a claim that a step has finished.
 */
export function AskProgress() {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const t0 = Date.now();
    const id = setInterval(() => setElapsed(Date.now() - t0), 250);
    return () => clearInterval(id);
  }, []);
  const current = elapsed < 1200 ? 0 : elapsed < 3500 ? 1 : 2;
  return (
    <div className="rounded-card border border-rule bg-paper p-5">
      <p role="status" aria-live="polite" className="sr-only">{PHASES[current]}…</p>
      <ol className="space-y-2" aria-hidden="true">
        {PHASES.map((p, i) => (
          <li key={p} className={`flex items-center gap-3 text-sm ${
            i === current ? 'font-medium text-ink' : i < current ? 'text-ink-2' : 'text-muted'}`}>
            <span className={`h-2 w-2 shrink-0 rounded-full ${
              i === current ? 'animate-pulse bg-accent' : i < current ? 'bg-ink-2' : 'bg-rule'}`} />
            {p}{i === current ? '…' : ''}
          </li>
        ))}
      </ol>
      <p className="mt-3 font-mono text-2xs tabular-nums text-muted">{(elapsed / 1000).toFixed(1)} s</p>
    </div>
  );
}

export function AnswerCard({ sentence, result }: { sentence: string; result: QueryResult }) {
  return (
    <section aria-labelledby="answer-heading" className="rounded-card border border-rule bg-paper p-5 sm:p-6">
      <h2 id="answer-heading" className="font-mono text-2xs uppercase tracking-[0.08em] text-muted">
        Answer
      </h2>
      <p className="mt-2 font-display text-xl leading-snug text-ink sm:text-2xl">{sentence}</p>
      <p className="mt-3 text-xs text-muted">
        {result.row_count} row{result.row_count === 1 ? '' : 's'} · computed by PostgreSQL in{' '}
        {(result.total_duration_ms / 1000).toFixed(1)} s · the model wrote the query, not the numbers
      </p>
    </section>
  );
}

function truncate(s: string, n: number) {
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}

export function ResultChart({ spec, result }: { spec: NonNullable<ChartSpec>; result: QueryResult }) {
  const label = (r: Record<string, unknown>) =>
    (spec.labels ?? [spec.x]).map((c) => String(r[c])).join(' + ');
  const data = result.rows.map((r) => ({
    label: spec.type === 'bar' ? label(r) : Number(r[spec.x]),
    value: Number(r[spec.y]),
  }));
  const yName = humanize(spec.y);
  const summary = spec.type === 'bar'
    ? `Bar chart of ${yName} for ${data.length} ${spec.labels?.length === 2 ? 'pairs' : 'items'}; the same figures are in the table below.`
    : `Line chart of ${yName} by ${humanize(spec.x)}; the same figures are in the table below.`;
  const fmt = (v: number) => formatValue(spec.y, v);

  return (
    <figure className="rounded-card border border-rule bg-paper p-5" aria-label={summary}>
      <figcaption className="mb-3 text-sm font-medium text-ink-2">
        {yName.charAt(0).toUpperCase() + yName.slice(1)}
      </figcaption>
      <div style={{ height: spec.type === 'bar' ? Math.max(160, data.length * 38) : 260 }}>
        <ResponsiveContainer width="100%" height="100%">
          {spec.type === 'bar' ? (
            // Horizontal bars: ranking labels ("CEREAL/BREAKFAST + FROZEN") stay
            // readable on a phone, where vertical-bar labels would collide.
            <BarChart data={data} layout="vertical" margin={{ left: 4, right: 16 }}>
              <CartesianGrid {...GRID_PROPS} horizontal={false} />
              <XAxis type="number" tick={AXIS_TICK} tickFormatter={fmt} />
              <YAxis type="category" dataKey="label" width={132} tick={AXIS_TICK}
                     tickFormatter={(v: string) => truncate(v, 20)} />
              <Tooltip {...TOOLTIP_STYLE} formatter={(v: number) => [fmt(v), yName]} />
              <Bar dataKey="value" fill={CHART.model} radius={[0, 3, 3, 0]} />
            </BarChart>
          ) : (
            <LineChart data={data} margin={{ left: 4, right: 16 }}>
              <CartesianGrid {...GRID_PROPS} />
              <XAxis dataKey="label" tick={AXIS_TICK} />
              <YAxis tick={AXIS_TICK} tickFormatter={fmt} width={72} />
              <Tooltip {...TOOLTIP_STYLE} formatter={(v: number) => [fmt(v), yName]} />
              <Line dataKey="value" stroke={CHART.model} strokeWidth={2} dot={false} />
            </LineChart>
          )}
        </ResponsiveContainer>
      </div>
    </figure>
  );
}

export const TABLE_ROWS_SHOWN = 50;

export function ResultTable({ result }: { result: QueryResult }) {
  const numeric = (c: string) => result.rows.every((r) => typeof r[c] === 'number'
    || (typeof r[c] === 'string' && /^-?\d+(\.\d+)?$/.test(String(r[c]))));
  return (
    <div className="rounded-card border border-rule bg-paper">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <caption className="px-4 pt-3 text-left text-xs text-muted">
            Result rows{result.row_count > TABLE_ROWS_SHOWN
              ? ` — first ${TABLE_ROWS_SHOWN} of ${result.row_count}` : ''}
          </caption>
          <thead>
            <tr className="border-b border-rule text-left text-xs uppercase tracking-wide text-muted">
              {result.columns.map((c) => (
                <th key={c} scope="col" className={`px-4 py-2 font-medium ${numeric(c) ? 'text-right' : ''}`}>
                  {humanize(c)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {result.rows.slice(0, TABLE_ROWS_SHOWN).map((r, i) => (
              <tr key={i} className="border-b border-rule last:border-0">
                {result.columns.map((c) => (
                  <td key={c} className={`px-4 py-2 ${numeric(c) ? 'text-right tabular-nums' : ''}`}>
                    {formatValue(c, r[c])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** Every attempt the model made, the checks each passed or failed, and what
 *  was fed back after a rejection. */
export function ValidationTrail({ attempts }: { attempts: Attempt[] }) {
  return (
    <div className="space-y-4">
      {attempts.map((a) => (
        <div key={a.attempt}>
          <h3 className="text-sm font-medium text-ink">
            Attempt {a.attempt}{a.rejected_reason ? ' — rejected' : ''}
          </h3>
          {a.sql && (
            <pre className="mt-2 overflow-x-auto rounded-input bg-ink p-3 font-mono text-xs leading-relaxed text-paper">
              {a.sql}
            </pre>
          )}
          <ul className="mt-2 space-y-1">
            {a.stages.map((s) => (
              <li key={s.stage} className="flex flex-wrap items-baseline gap-x-2 text-xs">
                <span className={`font-semibold ${s.passed ? 'text-positive' : 'text-negative'}`}>
                  {s.passed ? 'Passed' : 'Rejected'}
                </span>
                <span className="text-ink-2">{CHECK_HELP[s.stage] ?? s.stage}</span>
                {s.detail && <span className="text-muted">({s.detail})</span>}
              </li>
            ))}
          </ul>
          {a.error_fed_back && (
            <p className="mt-2 text-xs text-muted">
              Fed back to the model: <code className="font-mono">{a.error_fed_back}</code>
            </p>
          )}
        </div>
      ))}
    </div>
  );
}

/** "Why should I trust this?" -- only what the response and /health report. */
export function EvidencePanel({ e, attempts }: { e: Evidence; attempts: Attempt[] }) {
  return (
    <Disclosure summary="Why should I trust this?">
      <dl>
        <Row term="Data">
          {e.source
            ? <>The {e.source.tier} tier{e.source.tables ? ` — ${e.source.tables} pre-computed tables` : ''}
                {e.source.publishedAt ? `, published ${e.source.publishedAt.slice(0, 10)}` : ''}.</>
            : 'The data tier was not reported.'}
          {e.tables.length > 0 && (
            <span className="mt-1 block">
              Read from: {e.tables.map((t) => <code key={t} className="mr-2 font-mono text-xs">{t}</code>)}
            </span>
          )}
        </Row>
        {e.sql && (
          <Row term="Calculation">
            This is the exact query PostgreSQL ran:
            <pre className="mt-2 overflow-x-auto rounded-input bg-ink p-3 font-mono text-xs leading-relaxed text-paper">
              {e.sql}
            </pre>
          </Row>
        )}
        {e.checks.length > 0 && (
          <Row term="Checks">
            <ul className="space-y-1">
              {e.checks.map((c) => (
                <li key={c.stage}>
                  <span className={`font-semibold ${c.passed ? 'text-positive' : 'text-negative'}`}>
                    {c.passed ? 'Passed' : 'Rejected'}
                  </span>{' '}
                  {CHECK_HELP[c.stage] ?? c.stage}
                </li>
              ))}
            </ul>
            {e.attempts > 1 && (
              <p className="mt-1 text-xs text-muted">
                Answered on attempt {e.attempts}; earlier attempts were rejected and corrected.
              </p>
            )}
          </Row>
        )}
        {e.revenueBasis && (
          <Row term="Definition"><RevenueCaveat /></Row>
        )}
        <Row term="Limitations">
          <ul className="list-disc space-y-1 pl-4">
            {e.limitations.map((l) => <li key={l}>{l}</li>)}
          </ul>
        </Row>
      </dl>
      {attempts.length > 0 && (
        <div className="mt-4">
          <Disclosure summary="Full validation trail (technical)">
            <ValidationTrail attempts={attempts} />
          </Disclosure>
        </div>
      )}
    </Disclosure>
  );
}
