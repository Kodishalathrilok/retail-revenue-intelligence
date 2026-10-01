'use client';

/**
 * Overview building blocks. Every figure shown is a value an endpoint
 * returned; lib/overview.mjs only picks rows and words the result.
 */

import {
  CartesianGrid, Line, LineChart, ReferenceArea, ReferenceDot, ResponsiveContainer,
  Tooltip, XAxis, YAxis,
} from 'recharts';
import { AXIS_TICK, CHART, GRID_PROPS, TOOLTIP_STYLE } from '@/components/chart-theme';
import { Badge, Disclosure, type Tone } from '@/components/ui';
import { fmtMoney, fmtNum } from '@/lib/api';
import { effectSummary, signedMoney, weeklyAlt } from '@/lib/overview.mjs';

export type Weekly = {
  week_no: number; start_date: string; is_partial_week: boolean; revenue: string;
  baskets: number; households: number; rolling_7wk_avg: string | null;
};
export type Flagged = { week_no: number; revenue: string; z_score: string; is_partial_week: boolean };
export type Segment = {
  segment: string; households: number; pct_of_panel: string; pct_of_revenue: string;
  segment_revenue: string;
};
export type Forecast = {
  department: string; forecast_week: number; prediction: number;
  lower_bound: number; upper_bound: number;
  interval: { level: number; method: string; measured_coverage: number | null };
  confidence: string; caveats: string[];
  explanation: { description: string };
  accuracy: { test_weeks: number[]; department_wape: number | null };
};
export type Campaign = {
  campaign_id: number; treated_n: number; control_n: number; contaminated_pct: number;
  did_estimate: number; did_pvalue: number; ci_low: number; ci_high: number;
  confidence: string; warnings: string[];
  parallel_trends: { passed: boolean; verdict: string };
};

const compactUsd = new Intl.NumberFormat('en-US', {
  style: 'currency', currency: 'USD', notation: 'compact', maximumFractionDigits: 1,
});

function Swatch({ color, label, line = false }: { color: string; label: string; line?: boolean }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span aria-hidden="true" className={line ? 'h-0.5 w-4' : 'h-3 w-3 rounded-sm'}
            style={{ background: color }} />
      {label}
    </span>
  );
}

/**
 * Weekly revenue with the enrolment period shaded.
 *
 * The rise over the first weeks is households joining the panel (88 shopped in
 * week 1, about 1,300 from week 16). Drawn bare, it reads as revenue growth --
 * which is what this chart used to do. The shading and its label say what the
 * rise is; the forecasting contract ignores the same weeks for the same reason.
 */
export function WeeklyChart({ weekly, flagged, floor, department }: {
  weekly: Weekly[]; flagged: Flagged[]; floor: number; department: string;
}) {
  const first = weekly[0].week_no;
  const last = weekly[weekly.length - 1].week_no;
  const data = weekly.map((w) => ({
    week: w.week_no,
    revenue: Number(w.revenue),
    trend: w.rolling_7wk_avg === null ? null : Number(w.rolling_7wk_avg),
  }));
  const ticks = [first, ...Array.from({ length: 10 }, (_, i) => (i + 1) * 10)
    .filter((t) => t > first && t <= last)];
  const enrolling = first < floor;

  return (
    <figure aria-label={weeklyAlt(weekly, { department, floor, flaggedWeeks: flagged.map((f) => f.week_no) })}>
      <div className="h-64 sm:h-72">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
            <CartesianGrid {...GRID_PROPS} />
            <XAxis dataKey="week" type="number" domain={[first, last]} ticks={ticks} tick={AXIS_TICK} />
            <YAxis tick={AXIS_TICK} width={52} tickFormatter={(v: number) => compactUsd.format(v)} />
            <Tooltip {...TOOLTIP_STYLE} labelFormatter={(l) => `Week ${l}`}
                     formatter={(v: number, name: string) => [fmtMoney(v), name]} />
            {enrolling && (
              <ReferenceArea x1={first} x2={floor - 0.5} fill={CHART.band} stroke="none" />
            )}
            <Line type="monotone" dataKey="revenue" name="Weekly revenue" stroke={CHART.context}
                  dot={false} isAnimationActive={false} />
            <Line type="monotone" dataKey="trend" name="7-week average" stroke={CHART.ink}
                  strokeWidth={2} dot={false} isAnimationActive={false} />
            {flagged.map((f) => (
              <ReferenceDot key={f.week_no} x={f.week_no} y={Number(f.revenue)} r={4}
                            fill={CHART.compare} stroke="var(--color-paper)" />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
      <figcaption className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
        <Swatch line color={CHART.context} label="Weekly revenue" />
        <Swatch line color={CHART.ink} label="7-week centred average" />
        {enrolling && (
          <Swatch color={CHART.band}
                  label={`Weeks ${first}–${floor - 1}: households still joining the panel, not demand`} />
        )}
        {flagged.length > 0 && <Swatch color={CHART.compare} label="Flagged week" />}
      </figcaption>
    </figure>
  );
}

/** The chart's figures as a table: the text alternative, and the household
 *  counts that show the enrolment ramp directly. */
export function WeeklyTable({ weekly, flagged, floor }: {
  weekly: Weekly[]; flagged: Flagged[]; floor: number;
}) {
  const isFlagged = new Set(flagged.map((f) => f.week_no));
  return (
    <Disclosure summary="View the weekly figures as a table">
      <div className="max-h-80 overflow-auto">
        <table className="w-full text-sm">
          <caption className="sr-only">Weekly revenue and households shopping, by panel week</caption>
          <thead className="text-left text-xs uppercase tracking-wide text-muted">
            <tr>
              <th scope="col" className="py-1 pr-4 font-medium">Week</th>
              <th scope="col" className="py-1 pr-4 font-medium">Starts</th>
              <th scope="col" className="py-1 pr-4 text-right font-medium">Revenue</th>
              <th scope="col" className="py-1 pr-4 text-right font-medium">Households</th>
              <th scope="col" className="py-1 font-medium">Note</th>
            </tr>
          </thead>
          <tbody>
            {weekly.map((w) => (
              <tr key={w.week_no} className="border-t border-rule text-ink-2">
                <th scope="row" className="py-1 pr-4 text-left font-normal tabular-nums">{w.week_no}</th>
                <td className="whitespace-nowrap py-1 pr-4 tabular-nums">{w.start_date}</td>
                <td className="py-1 pr-4 text-right tabular-nums">{fmtMoney(w.revenue)}</td>
                <td className="py-1 pr-4 text-right tabular-nums">{fmtNum(w.households)}</td>
                <td className="py-1 text-xs text-muted">
                  {[w.is_partial_week && 'partial week', w.week_no < floor && 'enrolment',
                    isFlagged.has(w.week_no) && 'flagged'].filter(Boolean).join(' · ')}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Disclosure>
  );
}

/** Each segment's share of revenue as a bar on a common 0–100% scale. The
 *  figures are in the text, so the bars are decoration for a screen reader. */
export function SegmentShare({ segments }: { segments: Segment[] }) {
  return (
    <ul className="space-y-2.5">
      {segments.map((s) => (
        <li key={s.segment}>
          <div className="flex flex-wrap items-baseline justify-between gap-x-3 text-sm">
            <span>
              <span className="font-medium text-ink">{s.segment}</span>
              <span className="text-muted"> · {fmtNum(s.households)} households</span>
            </span>
            <span className="tabular-nums text-ink-2">{s.pct_of_revenue}% of revenue</span>
          </div>
          <div aria-hidden="true" className="mt-1 h-2 rounded-full bg-paper-3">
            <div className="h-2 rounded-full bg-accent" style={{ width: `${Number(s.pct_of_revenue)}%` }} />
          </div>
        </li>
      ))}
    </ul>
  );
}

const CONFIDENCE_TONE: Record<string, Tone> = {
  NORMAL: 'positive', LIMITED: 'caution', LOW: 'negative',
  CREDIBLE: 'positive', WEAK: 'caution', 'NOT CREDIBLE': 'negative',
};

function Headline({ label, value, children }: {
  label: string; value: string; children: React.ReactNode;
}) {
  return (
    <div>
      <p className="text-xs uppercase tracking-wide text-muted">{label}</p>
      <p className="mt-1 break-words font-display text-3xl leading-tight text-ink tabular-nums sm:text-4xl">
        {value}
      </p>
      <div className="mt-3 space-y-2 text-sm text-ink-2">{children}</div>
    </div>
  );
}

export function ForecastSummary({ f }: { f: Forecast }) {
  const level = (f.interval.level * 100).toFixed(0);
  return (
    <Headline label={`${f.department} · week ${f.forecast_week} forecast`} value={fmtMoney(f.prediction)}>
      <p>
        Likely range <span className="font-medium text-ink tabular-nums">
          {fmtMoney(f.lower_bound)} – {fmtMoney(f.upper_bound)}
        </span> ({level}% prediction interval).
      </p>
      {f.accuracy.department_wape !== null && (
        <p>
          On held-out weeks {f.accuracy.test_weeks[0]}–{f.accuracy.test_weeks[1]} the forecast for this
          department was off by <span className="font-medium text-ink tabular-nums">
            {f.accuracy.department_wape.toFixed(1)}%
          </span> of revenue on average.
        </p>
      )}
      <p className="flex flex-wrap items-center gap-2">
        <Badge tone={CONFIDENCE_TONE[f.confidence] ?? 'neutral'}>Confidence: {f.confidence}</Badge>
      </p>
      <p className="text-muted">{f.explanation.description} It is one week ahead only.</p>
    </Headline>
  );
}

export function CampaignSummary({ a }: { a: Campaign }) {
  const { sentence } = effectSummary(a);
  return (
    <Headline label={`Campaign ${a.campaign_id} · estimated effect on weekly spend`}
              value={signedMoney(a.did_estimate)}>
      <p>{sentence}</p>
      <p className="flex flex-wrap items-center gap-2">
        <Badge tone={CONFIDENCE_TONE[a.confidence] ?? 'neutral'}>Evidence: {a.confidence}</Badge>
        <span className="text-xs text-muted">p = {a.did_pvalue.toFixed(3)}</span>
      </p>
      <p className="text-muted">
        {fmtNum(a.treated_n)} campaign households compared with {fmtNum(a.control_n)} others;{' '}
        {a.contaminated_pct}% were also in an overlapping campaign. Campaigns were targeted, not
        randomised.
      </p>
    </Headline>
  );
}
