'use client';

/**
 * Customer-analytics building blocks. Every figure shown is a value the
 * /segments/rfm and /retention/tenure endpoints returned; lib/customers.mjs
 * only pivots rows for the chart and picks the leading segment.
 */

import {
  CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { AXIS_TICK, CHART, GRID_PROPS, TOOLTIP_STYLE } from '@/components/chart-theme';
import { fmtMoney, fmtNum } from '@/lib/api';
import { retentionSeries } from '@/lib/customers.mjs';

export type Segment = {
  segment: string; households: number; pct_of_panel: string; pct_of_revenue: string;
  segment_revenue: string; avg_recency_days: string; avg_baskets: string;
  avg_lifetime_value: string;
};
export type RetentionRow = {
  segment: string; tenure_month: number; cohort_size: number;
  active_households: number; retention_pct: string;
};

// One colour per onboarding group, in the low -> high order lib/customers.mjs
// emits. All three are theme tokens so the chart follows light/dark.
const GROUP_COLOR = [CHART.context, CHART.model, CHART.ink] as const;

function Swatch({ color, label }: { color: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span aria-hidden="true" className="h-0.5 w-4" style={{ background: color }} />
      {label}
    </span>
  );
}

/**
 * Retention curves by first-basket tercile, over relative tenure. The groups
 * start together at 100% in month 0 by construction; the curves show whether a
 * bigger first basket predicts stickier households (here, barely — they track
 * closely), and the caption marks where the panel window thins the cohort.
 */
export function RetentionCurves({ rows }: { rows: RetentionRow[] }) {
  const { data, segments } = retentionSeries(rows);
  const color = (i: number) => GROUP_COLOR[i] ?? CHART.compare;
  const summary =
    `Retention by relative tenure month for ${segments.length} first-basket groups; `
    + 'the same figures are in the table below.';

  return (
    <figure aria-label={summary}>
      <div className="h-64 sm:h-72">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 8, right: 12, bottom: 4, left: 0 }}>
            <CartesianGrid {...GRID_PROPS} />
            <XAxis dataKey="tenure_month" type="number" tick={AXIS_TICK}
                   domain={['dataMin', 'dataMax']}
                   label={{ value: 'Tenure month', position: 'insideBottom', offset: -2,
                            fontSize: 11, fill: 'var(--chart-axis)' }} />
            <YAxis tick={AXIS_TICK} width={40} domain={[0, 100]}
                   tickFormatter={(v: number) => `${v}%`} />
            <Tooltip {...TOOLTIP_STYLE}
                     labelFormatter={(l) => `Tenure month ${l}`}
                     formatter={(v: number, name: string) => [v === null ? '—' : `${v}%`, name]} />
            {segments.map((seg, i) => (
              <Line key={seg} type="monotone" dataKey={seg} name={seg} stroke={color(i)}
                    strokeWidth={2} dot={false} connectNulls={false} isAnimationActive={false} />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
      <figcaption className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
        {segments.map((seg, i) => <Swatch key={seg} color={color(i)} label={seg} />)}
      </figcaption>
    </figure>
  );
}

/** Per-segment purchasing behaviour: the recency, frequency and monetary
 *  figures behind the RFM groups, as a table. */
export function RfmBehaviourTable({ segments }: { segments: Segment[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <caption className="sr-only">
          RFM segments with household count, share of revenue, and average recency, baskets and lifetime value
        </caption>
        <thead className="text-left text-xs uppercase tracking-wide text-muted">
          <tr>
            <th scope="col" className="py-1 pr-4 font-medium">Segment</th>
            <th scope="col" className="py-1 pr-4 text-right font-medium">Households</th>
            <th scope="col" className="py-1 pr-4 text-right font-medium">% revenue</th>
            <th scope="col" className="py-1 pr-4 text-right font-medium">Avg recency (days)</th>
            <th scope="col" className="py-1 pr-4 text-right font-medium">Avg baskets</th>
            <th scope="col" className="py-1 text-right font-medium">Avg lifetime value</th>
          </tr>
        </thead>
        <tbody>
          {segments.map((s) => (
            <tr key={s.segment} className="border-t border-rule text-ink-2">
              <th scope="row" className="py-1 pr-4 text-left font-medium text-ink">{s.segment}</th>
              <td className="py-1 pr-4 text-right tabular-nums">{fmtNum(s.households)}</td>
              <td className="py-1 pr-4 text-right tabular-nums">{s.pct_of_revenue}%</td>
              <td className="py-1 pr-4 text-right tabular-nums">{fmtNum(s.avg_recency_days)}</td>
              <td className="py-1 pr-4 text-right tabular-nums">{fmtNum(s.avg_baskets)}</td>
              <td className="py-1 text-right tabular-nums">{fmtMoney(s.avg_lifetime_value)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
