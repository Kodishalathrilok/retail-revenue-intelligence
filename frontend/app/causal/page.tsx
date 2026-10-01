'use client';

/**
 * Causal view.
 *
 * Two things are shown side by side deliberately:
 *   1. the naive before/after next to the DiD estimate, because the gap between
 *      them is the point;
 *   2. campaign 26 (6.6% contaminated) next to campaign 18 (90.8%), because a
 *      contaminated estimate beside a clean one demonstrates what contamination
 *      does in a way a caveat cannot.
 */

import { useEffect, useState } from 'react';
import {
  CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { CaveatBar } from '@/components/Caveats';
import {
  AXIS_TICK, CHART, GRID_PROPS, LEGEND_STYLE, TOOLTIP_STYLE,
} from '@/components/chart-theme';
import {
  Badge, Callout, ErrorState, PageHeader, Skeleton, Stat, type Tone,
} from '@/components/ui';
import { Workflow } from '@/components/workflow';
import { fmtNum, get } from '@/lib/api';

type Analysis = {
  campaign_id: number; treated_n: number; control_n: number; contaminated_pct: number;
  naive_difference: number; did_estimate: number; did_stderr: number; did_pvalue: number;
  ci_low: number; ci_high: number; adjusted_estimate: number | null;
  adjusted_stderr: number | null; confounders_used: string[];
  parallel_trends: {
    passed: boolean; treated_slope: number; control_slope: number;
    interaction_pvalue: number; pre_weeks: number; verdict: string;
  };
  pre_period_series: {
    treated: { week_no: number; mean_spend: number }[];
    control: { week_no: number; mean_spend: number }[];
  };
  confidence: string; warnings: string[];
};

const VERDICT_TONE: Record<string, Tone> = {
  CREDIBLE: 'positive',
  WEAK: 'caution',
  'NOT CREDIBLE': 'negative',
};

function AnalysisPanel({ a }: { a: Analysis }) {
  const merged = a.pre_period_series.treated.map((t) => ({
    week: t.week_no,
    treated: t.mean_spend,
    control: a.pre_period_series.control.find((c) => c.week_no === t.week_no)?.mean_spend ?? null,
  }));
  const chartSummary = `Line chart of mean weekly spend per household over the `
    + `${a.parallel_trends.pre_weeks} weeks before campaign ${a.campaign_id}, treated against `
    + `control. Slopes: treated ${a.parallel_trends.treated_slope.toFixed(4)}, control `
    + `${a.parallel_trends.control_slope.toFixed(4)}.`;

  return (
    <div className="space-y-5 rounded-card border border-rule bg-paper-2 p-5">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="font-display text-lg leading-tight text-ink">Campaign {a.campaign_id}</h2>
        <Badge tone={VERDICT_TONE[a.confidence] ?? 'neutral'}>{a.confidence}</Badge>
        <span className="text-xs text-muted">
          {fmtNum(a.treated_n)} treated · {fmtNum(a.control_n)} control ·{' '}
          <span className={a.contaminated_pct > 25 ? 'font-semibold text-negative' : ''}>
            {a.contaminated_pct}% contaminated
          </span>
        </span>
      </div>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        <Stat
          label="Naive before/after"
          value={a.naive_difference >= 0 ? `+${a.naive_difference.toFixed(3)}` : a.naive_difference.toFixed(3)}
          sub="Treated group only. What a dashboard reports when nobody asks about a control group."
        />
        <Stat
          emphasis
          label="Difference-in-differences"
          value={a.did_estimate >= 0 ? `+${a.did_estimate.toFixed(3)}` : a.did_estimate.toFixed(3)}
          sub={`95% CI [${a.ci_low.toFixed(2)}, ${a.ci_high.toFixed(2)}] · p = ${a.did_pvalue.toFixed(3)}`}
        />
        <Stat
          label="Adjusted for confounders"
          value={a.adjusted_estimate === null ? '—'
            : a.adjusted_estimate >= 0 ? `+${a.adjusted_estimate.toFixed(3)}` : a.adjusted_estimate.toFixed(3)}
          sub={a.confounders_used.join(', ') || 'none'}
        />
      </div>

      <div className={`rounded-card border p-4 ${a.parallel_trends.passed
        ? 'border-positive-rule bg-positive-bg' : 'border-negative-rule bg-negative-bg'}`}>
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={a.parallel_trends.passed ? 'positive' : 'negative'}>
            {/* "Not rejected", never "hold": a pass on this low-power test is
                absence of evidence of a pre-trend, as the verdict below says. */}
            PARALLEL TRENDS {a.parallel_trends.passed ? 'NOT REJECTED' : 'VIOLATED'}
          </Badge>
          <span className="text-xs text-ink-2">
            interaction p = {a.parallel_trends.interaction_pvalue.toFixed(4)} ·{' '}
            {a.parallel_trends.pre_weeks} pre-period weeks
          </span>
        </div>
        <p className="mt-2 text-sm text-ink-2">{a.parallel_trends.verdict}</p>
      </div>

      <figure className="rounded-card border border-rule bg-paper p-4" aria-label={chartSummary}>
        <h3 className="text-sm font-semibold text-ink">Pre-period trends</h3>
        <p className="mb-2 text-xs text-muted">
          Mean weekly spend per household BEFORE the campaign. If these diverge,
          difference-in-differences attributes that divergence to the campaign.
        </p>
        <ResponsiveContainer width="100%" height={220}>
          <LineChart data={merged}>
            <CartesianGrid {...GRID_PROPS} />
            <XAxis dataKey="week" tick={AXIS_TICK} />
            <YAxis tick={AXIS_TICK} />
            <Tooltip {...TOOLTIP_STYLE} />
            <Legend wrapperStyle={LEGEND_STYLE} />
            <Line type="monotone" dataKey="treated" stroke={CHART.compare} dot={false} name="treated" />
            <Line type="monotone" dataKey="control" stroke={CHART.ink} dot={false} name="control" />
          </LineChart>
        </ResponsiveContainer>
        <p className="mt-1 text-xs text-muted">
          slopes — treated {a.parallel_trends.treated_slope.toFixed(4)} ·
          control {a.parallel_trends.control_slope.toFixed(4)}
        </p>
      </figure>

      {a.warnings.length > 0 && (
        <Callout tone="caution" title={a.warnings.length === 1 ? 'Warning' : 'Warnings'}>
          <ul className="list-disc space-y-1.5 pl-4">
            {a.warnings.map((w) => <li key={w}>{w}</li>)}
          </ul>
        </Callout>
      )}
    </div>
  );
}

export default function CausalPage() {
  const [clean, setClean] = useState<Analysis | null>(null);
  const [stress, setStress] = useState<Analysis | null>(null);
  const [err, setErr] = useState<unknown>(null);
  // Campaign 18 used to fail silently (.catch(() => {})), so the contaminated
  // comparison could disappear without a word.
  const [stressErr, setStressErr] = useState<unknown>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    get<Analysis>('/api/v1/causal/analysis/26')
      .then((a) => { setClean(a); setErr(null); }).catch(setErr);
    get<Analysis>('/api/v1/causal/analysis/18')
      .then((a) => { setStress(a); setStressErr(null); }).catch(setStressErr);
  }, [attempt]);

  const retry = () => setAttempt((n) => n + 1);

  return (
    <div className="space-y-8">
      <PageHeader eyebrow="Investigate · campaign effects" title="Causal analysis">
        Difference-in-differences on a real dunnhumby campaign. SQL builds the
        panels, statsmodels estimates the effect, and the language model only
        proposes candidate confounders — it never produces an estimate.
      </PageHeader>

      {err != null && <ErrorState error={err} what="campaign 26" onRetry={retry} />}

      {clean ? <AnalysisPanel a={clean} /> : err == null && (
        <Skeleton label="the campaign analysis" className="h-96" />
      )}

      {stress && (
        <div>
          <div className="mb-3 rounded-card border border-rule bg-paper p-4">
            <h2 className="font-display text-lg leading-tight text-ink">Contaminated comparison</h2>
            <p className="mt-1 max-w-[70ch] text-sm text-ink-2">
              Campaign 18 is the largest campaign in the dataset and one of the
              worst candidates: 90.8% of its enrolled households were
              simultaneously in an overlapping campaign. It is shown here
              deliberately. Sorting campaigns by enrolment — the obvious choice —
              puts this one first.
            </p>
          </div>
          <AnalysisPanel a={stress} />
        </div>
      )}

      {stressErr != null && (
        <ErrorState error={stressErr} what="the contaminated comparison (campaign 18)"
                    onRetry={retry} />
      )}

      <Workflow current="investigate" heading="Where next" />

      <CaveatBar calendar panel revenue />
    </div>
  );
}
