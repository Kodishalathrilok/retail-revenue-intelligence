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

const VERDICT_STYLE: Record<string, string> = {
  CREDIBLE: 'bg-emerald-600',
  WEAK: 'bg-amber-600',
  'NOT CREDIBLE': 'bg-red-600',
};

function EstimateCard({ label, value, sub, emphasis }: {
  label: string; value: string; sub: string; emphasis?: boolean;
}) {
  return (
    <div className={`rounded-lg border p-4 ${emphasis
      ? 'border-slate-900 bg-slate-900 text-white' : 'border-slate-200 bg-white'}`}>
      <div className={`text-xs uppercase tracking-wide ${emphasis ? 'text-slate-300' : 'text-slate-500'}`}>
        {label}
      </div>
      <div className="mt-1 text-2xl font-semibold tabular-nums">{value}</div>
      <div className={`mt-1 text-xs ${emphasis ? 'text-slate-400' : 'text-slate-500'}`}>{sub}</div>
    </div>
  );
}

function AnalysisPanel({ a }: { a: Analysis }) {
  const merged = a.pre_period_series.treated.map((t) => ({
    week: t.week_no,
    treated: t.mean_spend,
    control: a.pre_period_series.control.find((c) => c.week_no === t.week_no)?.mean_spend ?? null,
  }));

  return (
    <div className="space-y-5 rounded-lg border border-slate-200 bg-slate-50/50 p-5">
      <div className="flex items-center gap-3">
        <h2 className="text-lg font-semibold">Campaign {a.campaign_id}</h2>
        <span className={`rounded px-2 py-0.5 text-xs font-semibold text-white ${
          VERDICT_STYLE[a.confidence] ?? 'bg-slate-600'}`}>
          {a.confidence}
        </span>
        <span className="text-xs text-slate-500">
          {fmtNum(a.treated_n)} treated · {fmtNum(a.control_n)} control ·{' '}
          <span className={a.contaminated_pct > 25 ? 'font-semibold text-red-600' : ''}>
            {a.contaminated_pct}% contaminated
          </span>
        </span>
      </div>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        <EstimateCard
          label="Naive before/after"
          value={a.naive_difference >= 0 ? `+${a.naive_difference.toFixed(3)}` : a.naive_difference.toFixed(3)}
          sub="Treated group only. What a dashboard reports when nobody asks about a control group."
        />
        <EstimateCard
          emphasis
          label="Difference-in-differences"
          value={a.did_estimate >= 0 ? `+${a.did_estimate.toFixed(3)}` : a.did_estimate.toFixed(3)}
          sub={`95% CI [${a.ci_low.toFixed(2)}, ${a.ci_high.toFixed(2)}] · p = ${a.did_pvalue.toFixed(3)}`}
        />
        <EstimateCard
          label="Adjusted for confounders"
          value={a.adjusted_estimate === null ? '—'
            : a.adjusted_estimate >= 0 ? `+${a.adjusted_estimate.toFixed(3)}` : a.adjusted_estimate.toFixed(3)}
          sub={a.confounders_used.join(', ') || 'none'}
        />
      </div>

      <div className={`rounded-lg border p-4 ${a.parallel_trends.passed
        ? 'border-emerald-200 bg-emerald-50' : 'border-red-300 bg-red-50'}`}>
        <div className="flex items-center gap-2">
          <span className={`rounded px-2 py-0.5 text-xs font-semibold text-white ${
            a.parallel_trends.passed ? 'bg-emerald-600' : 'bg-red-600'}`}>
            PARALLEL TRENDS {a.parallel_trends.passed ? 'HOLD' : 'VIOLATED'}
          </span>
          <span className="text-xs text-slate-600">
            interaction p = {a.parallel_trends.interaction_pvalue.toFixed(4)} ·{' '}
            {a.parallel_trends.pre_weeks} pre-period weeks
          </span>
        </div>
        <p className="mt-2 text-sm text-slate-700">{a.parallel_trends.verdict}</p>
      </div>

      <div className="rounded-lg border border-slate-200 bg-white p-4">
        <h3 className="text-sm font-semibold">Pre-period trends</h3>
        <p className="mb-2 text-xs text-slate-500">
          Mean weekly spend per household BEFORE the campaign. If these diverge,
          difference-in-differences attributes that divergence to the campaign.
        </p>
        <ResponsiveContainer width="100%" height={220}>
          <LineChart data={merged}>
            <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
            <XAxis dataKey="week" tick={{ fontSize: 11 }} />
            <YAxis tick={{ fontSize: 11 }} />
            <Tooltip />
            <Legend wrapperStyle={{ fontSize: 12 }} />
            <Line type="monotone" dataKey="treated" stroke="#dc2626" dot={false} name="treated" />
            <Line type="monotone" dataKey="control" stroke="#0f172a" dot={false} name="control" />
          </LineChart>
        </ResponsiveContainer>
        <p className="mt-1 text-xs text-slate-400">
          slopes — treated {a.parallel_trends.treated_slope.toFixed(4)} ·
          control {a.parallel_trends.control_slope.toFixed(4)}
        </p>
      </div>

      {a.warnings.length > 0 && (
        <ul className="space-y-1.5 rounded-lg border border-amber-200 bg-amber-50 p-4">
          {a.warnings.map((w, i) => (
            <li key={i} className="text-sm text-amber-900">⚠ {w}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default function CausalPage() {
  const [clean, setClean] = useState<Analysis | null>(null);
  const [stress, setStress] = useState<Analysis | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    get<Analysis>('/api/v1/causal/analysis/26').then(setClean).catch((e) => setErr(String(e)));
    get<Analysis>('/api/v1/causal/analysis/18').then(setStress).catch(() => {});
  }, []);

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Causal analysis</h1>
        <p className="mt-1 max-w-3xl text-sm text-slate-500">
          Difference-in-differences on a real dunnhumby campaign. SQL builds the
          panels, statsmodels estimates the effect, and the language model only
          proposes candidate confounders — it never produces an estimate.
        </p>
      </div>

      {err && (
        <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          {err} — is the API running? <code>rrip serve</code>
        </div>
      )}

      {clean ? <AnalysisPanel a={clean} /> : !err && (
        <div className="py-16 text-center text-sm text-slate-400">Computing…</div>
      )}

      {stress && (
        <div>
          <div className="mb-3 rounded-lg border border-slate-300 bg-white p-4">
            <h2 className="text-sm font-semibold">Contaminated comparison</h2>
            <p className="mt-1 text-sm text-slate-600">
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

      <CaveatBar calendar panel revenue />
    </div>
  );
}
