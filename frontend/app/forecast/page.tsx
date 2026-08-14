'use client';

/**
 * Forecast view.
 *
 * The design decision worth stating: this page leads with the deployment
 * result, not with the forecast. The headline banner says that the tested ML
 * models did not beat a four-week trailing mean and that the trailing mean is
 * therefore what runs.
 *
 * That is deliberate. A forecasting page that opens with a big number and
 * mentions the benchmark in a footnote invites the reader to trust the number
 * more than the evidence supports. The business tool this is meant to be needs
 * the reader to know, before they act, that this is a smoothed recent average
 * with a measured error band -- not a model that has found something.
 *
 * Three things are shown next to every forecast for the same reason:
 *   - the prediction interval, because a point estimate presented alone reads
 *     as certainty;
 *   - the per-department measured test error, because it ranges from 7% to 68%
 *     and a single headline figure would misrepresent most departments;
 *   - the four weekly figures that were averaged, which reconcile to the
 *     forecast exactly.
 */

import { useEffect, useState } from 'react';
import {
  Area, CartesianGrid, ComposedChart, Legend, Line, ReferenceLine,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { fmtMoney, fmtNum, get } from '@/lib/api';

type DeptItem = {
  department: string;
  test_wape: number | null;
  servable: boolean;
  trailing_scale_usd: number | null;
};

type Contributor = { label: string; value_usd?: number; weight?: number };

type Forecast = {
  target: string; unit: string; department: string;
  forecast_week: number; forecast_origin_week: number; horizon_weeks: number;
  prediction: number; lower_bound: number; upper_bound: number;
  interval: { level: number; kind: string; method: string;
              measured_coverage: number | null };
  baseline: { name: string; prediction: number };
  challenger_model: { name: string | null; prediction: number | null;
                      deployed: boolean };
  actual: number | null;
  confidence: 'NORMAL' | 'LIMITED' | 'LOW';
  confidence_reasons: string[];
  caveats: string[];
  explanation: { method: string; additive: boolean; description: string;
                 contributors: Contributor[] };
  model_version: string; model_type: string;
  trained_until_week: number; observed_until_week: number;
  accuracy: { test_weeks: number[]; pooled_wape: number;
              department_wape: number | null };
};

type HistoryPoint = {
  week_no: number; split: string; actual: number | null; prediction: number;
  lower_bound: number; upper_bound: number; baseline_prediction: number;
  is_forecast: boolean;
};

type Summary = {
  model_type: string; deployed_kind: string; deployment_rationale: string;
  challenger: { name?: string; test_scores?: { wape: number } };
  windows: { train: number[]; validation: number[]; test: number[] };
  metrics: {
    test: { scores: { wape: number; mae: number } };
    test_baselines: Record<string, { wape: number; mae: number; rmse: number }>;
    macro_wape_test: number;
  };
  conformal: { test_coverage: Record<string, { coverage: number }> };
  leakage_audit_passed: boolean;
};

const CONFIDENCE_STYLE: Record<string, string> = {
  NORMAL: 'bg-emerald-600',
  LIMITED: 'bg-amber-600',
  LOW: 'bg-red-600',
};

function Stat({ label, value, sub, emphasis }: {
  label: string; value: string; sub?: string; emphasis?: boolean;
}) {
  return (
    <div className={`rounded-lg border p-4 ${emphasis
      ? 'border-slate-900 bg-slate-900 text-white' : 'border-slate-200 bg-white'}`}>
      <div className={`text-xs uppercase tracking-wide ${
        emphasis ? 'text-slate-300' : 'text-slate-500'}`}>{label}</div>
      <div className="mt-1 text-2xl font-semibold tabular-nums">{value}</div>
      {sub && <div className={`mt-1 text-xs ${
        emphasis ? 'text-slate-400' : 'text-slate-500'}`}>{sub}</div>}
    </div>
  );
}

export default function ForecastPage() {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [departments, setDepartments] = useState<DeptItem[]>([]);
  const [selected, setSelected] = useState<string>('GROCERY');
  const [forecast, setForecast] = useState<Forecast | null>(null);
  const [history, setHistory] = useState<HistoryPoint[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([
      get<Summary>('/api/v1/forecast/summary'),
      get<{ items: DeptItem[] }>('/api/v1/forecast/departments'),
    ])
      .then(([s, d]) => { setSummary(s); setDepartments(d.items); })
      .catch((e) => setError(String(e)));
  }, []);

  useEffect(() => {
    if (!selected) return;
    setLoading(true);
    setError(null);
    Promise.all([
      get<Forecast>(`/api/v1/forecast?department=${encodeURIComponent(selected)}`),
      get<{ series: HistoryPoint[] }>(
        `/api/v1/forecast/history/${encodeURIComponent(selected)}?weeks=30`),
    ])
      .then(([f, h]) => { setForecast(f); setHistory(h.series); })
      .catch((e) => { setForecast(null); setError(String(e)); })
      .finally(() => setLoading(false));
  }, [selected]);

  const chart = history.map((p) => ({
    week: p.week_no,
    actual: p.actual,
    forecast: p.is_forecast || p.split === 'test' ? p.prediction : null,
    // Recharts stacks an Area from a [low, high] tuple, which draws the band
    // rather than a filled region down to the axis.
    band: p.is_forecast || p.split === 'test'
      ? [p.lower_bound, p.upper_bound] : null,
  }));

  const baselines = summary
    ? Object.entries(summary.metrics.test_baselines)
        .map(([name, s]) => ({ name, ...s }))
        .sort((a, b) => a.wape - b.wape)
    : [];

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">
          Next-week revenue forecast
        </h1>
        <p className="mt-1 text-sm text-slate-600">
          One week ahead, by department. Every figure below was computed by a
          predictor scored on a held-out temporal test set — no language model
          produces any number on this page.
        </p>
      </div>

      {summary && (
        <div className="rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm">
          <div className="font-semibold text-amber-900">
            The machine-learning model did not earn deployment.
          </div>
          <p className="mt-1 text-amber-900/90">{summary.deployment_rationale}</p>
          <p className="mt-2 text-amber-900/70">
            What runs is a {summary.model_type.replace(/_/g, ' ')} — the mean of
            the last four completed weeks. It cannot anticipate a spike, a
            promotion, or a level shift, and the interval below is where the
            actual figure has historically landed.
          </p>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-3">
        <label className="text-sm text-slate-600" htmlFor="dept">Department</label>
        <select
          id="dept"
          className="rounded border border-slate-300 bg-white px-3 py-1.5 text-sm"
          value={selected}
          onChange={(e) => setSelected(e.target.value)}
        >
          {departments.map((d) => (
            <option key={d.department} value={d.department} disabled={!d.servable}>
              {d.department}
              {d.test_wape !== null ? ` — ${d.test_wape.toFixed(1)}% WAPE` : ''}
              {d.servable ? '' : ' (insufficient history)'}
            </option>
          ))}
        </select>
        {loading && <span className="text-xs text-slate-400">loading…</span>}
      </div>

      {error && (
        <div className="rounded-lg border border-red-300 bg-red-50 p-4 text-sm text-red-900">
          {error}
        </div>
      )}

      {forecast && (
        <>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Stat
              emphasis
              label={`Week ${forecast.forecast_week} forecast`}
              value={fmtMoney(forecast.prediction)}
              sub={`${forecast.department} · ${forecast.horizon_weeks} week ahead`}
            />
            <Stat
              label={`${(forecast.interval.level * 100).toFixed(0)}% prediction interval`}
              value={`${fmtMoney(forecast.lower_bound)} – ${fmtMoney(forecast.upper_bound)}`}
              sub={forecast.interval.measured_coverage !== null
                ? `measured coverage ${(forecast.interval.measured_coverage * 100).toFixed(1)}% on the test weeks`
                : 'coverage not measured'}
            />
            <Stat
              label="Measured error, this department"
              value={forecast.accuracy.department_wape !== null
                ? `${forecast.accuracy.department_wape.toFixed(1)}%`
                : '—'}
              sub={`WAPE on weeks ${forecast.accuracy.test_weeks[0]}–${forecast.accuracy.test_weeks[1]} · ${forecast.accuracy.pooled_wape.toFixed(1)}% pooled`}
            />
            <Stat
              label="Challenger model"
              value={forecast.challenger_model.prediction !== null
                ? fmtMoney(forecast.challenger_model.prediction) : '—'}
              sub={`${forecast.challenger_model.name ?? 'none'} — not deployed`}
            />
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <span className={`rounded px-2 py-0.5 text-xs font-semibold text-white ${
              CONFIDENCE_STYLE[forecast.confidence] ?? 'bg-slate-600'}`}>
              Confidence: {forecast.confidence}
            </span>
            <span className="text-xs text-slate-500">
              model {forecast.model_version} · observed through week{' '}
              {forecast.observed_until_week}
            </span>
          </div>

          {(forecast.confidence_reasons.length > 0 ||
            forecast.caveats.length > 0) && (
            <ul className="space-y-1 rounded-lg border border-slate-200 bg-slate-50 p-4 text-sm text-slate-700">
              {forecast.confidence_reasons.map((r) => (
                <li key={r}>• {r}</li>
              ))}
              {forecast.caveats.map((c) => (
                <li key={c}>• {c}</li>
              ))}
            </ul>
          )}

          <div className="rounded-lg border border-slate-200 bg-white p-5">
            <h2 className="text-sm font-semibold text-slate-700">
              Actuals, out-of-sample forecasts and the prediction interval
            </h2>
            <p className="mb-3 mt-1 text-xs text-slate-500">
              The forecast line is drawn only for the held-out test weeks and the
              future week. Training weeks are omitted from it because an
              in-sample fit is not evidence of forecast accuracy.
            </p>
            <div className="h-80">
              <ResponsiveContainer width="100%" height="100%">
                <ComposedChart data={chart}
                               margin={{ top: 5, right: 10, bottom: 5, left: 10 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                  <XAxis dataKey="week" tick={{ fontSize: 12 }}
                         label={{ value: 'week', position: 'insideBottom',
                                  offset: -3, fontSize: 11 }} />
                  <YAxis tick={{ fontSize: 12 }}
                         tickFormatter={(v) => fmtNum(v)} width={70} />
                  <Tooltip
                    formatter={(v: unknown, name: string) =>
                      Array.isArray(v)
                        ? [`${fmtMoney(v[0])} – ${fmtMoney(v[1])}`, 'interval']
                        : [fmtMoney(v as number), name]}
                    labelFormatter={(l) => `week ${l}`} />
                  <Legend wrapperStyle={{ fontSize: 12 }} />
                  <Area dataKey="band" name="prediction interval"
                        stroke="none" fill="#93c5fd" fillOpacity={0.35}
                        connectNulls={false} />
                  <Line dataKey="actual" name="actual" stroke="#0f172a"
                        strokeWidth={2} dot={false} connectNulls />
                  <Line dataKey="forecast" name="forecast" stroke="#2563eb"
                        strokeWidth={2} strokeDasharray="5 4" dot={false}
                        connectNulls />
                  <ReferenceLine x={forecast.observed_until_week}
                                 stroke="#94a3b8" strokeDasharray="2 2"
                                 label={{ value: 'last observed', fontSize: 10,
                                          position: 'top', fill: '#64748b' }} />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
          </div>

          <div className="grid gap-6 lg:grid-cols-2">
            <div className="rounded-lg border border-slate-200 bg-white p-5">
              <h2 className="text-sm font-semibold text-slate-700">
                Why this number
              </h2>
              <p className="mb-3 mt-1 text-xs text-slate-500">
                {forecast.explanation.description}
                {forecast.explanation.additive
                  ? ' These figures reconcile to the forecast exactly.'
                  : ' These are approximate attributions and do not sum to the forecast.'}
              </p>
              <table className="w-full text-sm">
                <tbody>
                  {forecast.explanation.contributors.map((c) => (
                    <tr key={c.label} className="border-t border-slate-100">
                      <td className="py-1.5 text-slate-600">{c.label}</td>
                      <td className="py-1.5 text-right tabular-nums">
                        {c.value_usd !== undefined ? fmtMoney(c.value_usd) : '—'}
                      </td>
                      <td className="w-16 py-1.5 text-right text-xs text-slate-400">
                        {c.weight !== undefined
                          ? `× ${c.weight.toFixed(2)}` : ''}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {summary && (
              <div className="rounded-lg border border-slate-200 bg-white p-5">
                <h2 className="text-sm font-semibold text-slate-700">
                  Every predictor on the held-out test weeks
                </h2>
                <p className="mb-3 mt-1 text-xs text-slate-500">
                  Weeks {summary.windows.test[0]}–{summary.windows.test[1]},
                  untouched during model selection. Sorted by WAPE.
                </p>
                <table className="w-full text-sm">
                  <thead className="text-xs uppercase tracking-wide text-slate-400">
                    <tr>
                      <th className="pb-1 text-left font-medium">predictor</th>
                      <th className="pb-1 text-right font-medium">MAE</th>
                      <th className="pb-1 text-right font-medium">WAPE</th>
                    </tr>
                  </thead>
                  <tbody>
                    {baselines.map((b) => {
                      const isDeployed = b.name === summary.model_type;
                      const isChallenger = b.name === '__challenger_model__';
                      return (
                        <tr key={b.name}
                            className={`border-t border-slate-100 ${
                              isDeployed ? 'font-semibold text-slate-900' : ''}`}>
                          <td className="py-1.5">
                            {isChallenger ? 'ML challenger' : b.name}
                            {isDeployed && (
                              <span className="ml-2 rounded bg-slate-900 px-1.5 py-0.5 text-[10px] uppercase text-white">
                                deployed
                              </span>
                            )}
                          </td>
                          <td className="py-1.5 text-right tabular-nums">
                            {fmtNum(b.mae, 0)}
                          </td>
                          <td className="py-1.5 text-right tabular-nums">
                            {b.wape.toFixed(2)}%
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
                <p className="mt-3 text-xs text-slate-500">
                  Pooled WAPE {summary.metrics.test.scores.wape.toFixed(2)}% is
                  dollar-weighted and GROCERY is half the revenue. Weighting every
                  department equally gives{' '}
                  {summary.metrics.macro_wape_test.toFixed(1)}%, which is the
                  figure that describes the system rather than its largest
                  department.
                </p>
              </div>
            )}
          </div>

          {summary && (
            <div className="rounded-lg border border-slate-200 bg-slate-50 p-4 text-xs text-slate-600">
              <span className="font-semibold text-slate-700">Basis.</span>{' '}
              Trained on weeks {summary.windows.train[0]}–
              {summary.windows.train[1]}; intervals calibrated on weeks{' '}
              {summary.windows.validation[0]}–{summary.windows.validation[1]};
              tested on weeks {summary.windows.test[0]}–{summary.windows.test[1]},
              which were locked during model selection. Leakage audit:{' '}
              {summary.leakage_audit_passed ? 'passed' : 'FAILED'}. Weeks 1 and
              102 are partial (5 and 6 days); no feature reads a week before 20,
              because 99.7% of the panel had been recruited by then and earlier
              weeks are enrolment rather than demand.
            </div>
          )}
        </>
      )}
    </div>
  );
}
