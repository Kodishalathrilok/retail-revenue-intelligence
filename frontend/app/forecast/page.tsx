'use client';

/**
 * Forecast view, business-readable first.
 *
 * Reading order: the number and its range, the history it sits in, how the
 * method was chosen and how wrong it has been, then the technical record
 * behind a disclosure.
 *
 * The page used to open with the deployment result (the tested ML model did
 * not beat a four-week trailing mean) above the forecast itself. That finding
 * has not become a footnote: it is the "How this forecast is made" section, in
 * plain words next to the measured error, so the reader still learns before
 * acting that this is a smoothed recent average with a measured error band --
 * not a model that has found something.
 *
 * Three things stay next to every forecast for the same reason:
 *   - the prediction interval, because a point estimate presented alone reads
 *     as certainty;
 *   - the department's own measured test error, because it ranges from 7% to
 *     68% and a single headline figure would misrepresent most departments;
 *   - the four weekly figures that were averaged, which reconcile to the
 *     forecast exactly.
 *
 * Every figure is a value an endpoint returned, and the Explain panels are
 * static text from lib/metrics.mjs.
 */

import { useEffect, useId, useState } from 'react';
import {
  Area, CartesianGrid, ComposedChart, Legend, Line, ReferenceLine,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import {
  AXIS_TICK, CHART, GRID_PROPS, LEGEND_STYLE, TOOLTIP_STYLE,
} from '@/components/chart-theme';
import { Explain } from '@/components/explain';
import {
  Badge, Disclosure, ErrorState, Fact, PageHeader, Section, Skeleton, Stat, type Tone,
} from '@/components/ui';
import { Workflow } from '@/components/workflow';
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

const CONFIDENCE_TONE: Record<string, Tone> = {
  NORMAL: 'positive',
  LIMITED: 'caution',
  LOW: 'negative',
};

const CHALLENGER = '__challenger_model__';

/** "trailing_mean_4" as a reader would say it. Worded as an average of past
 *  weeks so it cannot be read as a forecast four weeks out. */
const methodName = (modelType: string) =>
  modelType === 'trailing_mean_4' ? 'Average of the last four weeks' : modelType.replace(/_/g, ' ');

// The forecast and its range are drawn only where the predictor was not
// fitted: the held-out test weeks and the future week. An in-sample fit is
// not evidence of forecast accuracy.
const outOfSample = (p: HistoryPoint) => p.is_forecast || p.split === 'test';

const pct = (v: number, dp = 1) => `${v.toFixed(dp)}%`;

export default function ForecastPage() {
  const id = useId();
  const [summary, setSummary] = useState<Summary | null>(null);
  const [departments, setDepartments] = useState<DeptItem[]>([]);
  const [selected, setSelected] = useState<string>('GROCERY');
  const [forecast, setForecast] = useState<Forecast | null>(null);
  const [history, setHistory] = useState<HistoryPoint[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    Promise.all([
      get<Summary>('/api/v1/forecast/summary'),
      get<{ items: DeptItem[] }>('/api/v1/forecast/departments'),
    ])
      .then(([s, d]) => {
        setSummary(s);
        setDepartments(d.items);
        // ?department= arrives from the Overview. It is honoured only for a
        // department this endpoint serves, so a stale or mistyped link opens
        // the default instead of an error.
        const wanted = new URLSearchParams(window.location.search).get('department');
        if (wanted && d.items.some((x) => x.department === wanted && x.servable)) {
          setSelected(wanted);
        }
      })
      .catch(setError);
  }, [attempt]);

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
      .catch((e) => { setForecast(null); setError(e); })
      .finally(() => setLoading(false));
  }, [selected, attempt]);

  const chart = history.map((p) => ({
    week: p.week_no,
    actual: p.actual,
    forecast: outOfSample(p) ? p.prediction : null,
    // Recharts stacks an Area from a [low, high] tuple, which draws the band
    // rather than a filled region down to the axis.
    band: outOfSample(p) ? [p.lower_bound, p.upper_bound] : null,
  }));
  const lastObserved = [...history].reverse().find((p) => p.actual !== null);

  const baselines = summary
    ? Object.entries(summary.metrics.test_baselines)
        .map(([name, s]) => ({ name, ...s }))
        .sort((a, b) => a.wape - b.wape)
    : [];
  const deployed = summary?.metrics.test_baselines[summary.model_type];
  const challenger = summary?.metrics.test_baselines[CHALLENGER];
  const baselineRuns = summary?.deployed_kind === 'baseline';

  return (
    <div className="space-y-8">
      <PageHeader eyebrow="Predict · one week ahead" title="Next-week revenue forecast">
        Expected revenue for one department, one week ahead, with the range the
        actual figure is likely to fall in and how far off this method has been.
        Tested predictors compute every figure — no language model produces any
        number on this page.
      </PageHeader>

      <div className="flex flex-wrap items-center gap-3">
        <label className="text-sm text-ink-2" htmlFor={`${id}-dept`}>Department</label>
        <select
          id={`${id}-dept`}
          className="min-h-control max-w-full rounded-input border border-rule bg-paper px-3 text-sm text-ink"
          value={selected}
          onChange={(e) => setSelected(e.target.value)}
        >
          {departments.map((d) => (
            <option key={d.department} value={d.department} disabled={!d.servable}>
              {d.department}
              {d.test_wape !== null ? ` — typical error ${pct(d.test_wape)}` : ''}
              {d.servable ? '' : ' (insufficient history)'}
            </option>
          ))}
        </select>
        {loading && forecast && <span role="status" className="text-xs text-muted">loading…</span>}
      </div>

      {error != null && (
        <ErrorState error={error} what="the forecast"
                    onRetry={() => setAttempt((n) => n + 1)} />
      )}

      {!forecast && error == null && <Skeleton label="the forecast" className="h-64" />}

      {forecast && (
        <>
          <section aria-labelledby={`${id}-expect`} className="space-y-4">
            <h2 id={`${id}-expect`} className="font-display text-lg leading-tight text-ink">
              What to expect in week {forecast.forecast_week}
            </h2>
            <p className="max-w-[65ch] font-display text-xl leading-snug text-ink">
              {forecast.department} revenue is forecast at {fmtMoney(forecast.prediction)} for
              week {forecast.forecast_week}, with the actual figure expected between{' '}
              {fmtMoney(forecast.lower_bound)} and {fmtMoney(forecast.upper_bound)}.
            </p>

            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <Stat
                emphasis
                label={`Week ${forecast.forecast_week} forecast`}
                value={fmtMoney(forecast.prediction)}
                sub={`${forecast.department} · one week ahead`}
              />
              <Stat
                label="Forecast range"
                value={`${fmtMoney(forecast.lower_bound)} – ${fmtMoney(forecast.upper_bound)}`}
                sub={`${(forecast.interval.level * 100).toFixed(0)}% prediction interval`
                  + (forecast.interval.measured_coverage !== null
                    ? ` · held ${pct(forecast.interval.measured_coverage * 100)} of test-week actuals`
                    : '')}
              />
              <Stat
                label="Typical error, this department"
                value={forecast.accuracy.department_wape !== null
                  ? pct(forecast.accuracy.department_wape) : '—'}
                sub={`of revenue, measured on held-out weeks ${forecast.accuracy.test_weeks[0]}–${forecast.accuracy.test_weeks[1]}`}
              />
              {lastObserved && (
                <Stat
                  label={`Week ${lastObserved.week_no} actual`}
                  value={fmtMoney(lastObserved.actual)}
                  sub="the latest observed week"
                />
              )}
            </div>

            <div className="flex flex-wrap items-center gap-3">
              <Badge tone={CONFIDENCE_TONE[forecast.confidence] ?? 'neutral'}>
                Confidence: {forecast.confidence}
              </Badge>
              <span className="text-xs text-muted">
                observed through week {forecast.observed_until_week}
              </span>
            </div>

            {(forecast.confidence_reasons.length > 0 ||
              forecast.caveats.length > 0) && (
              <ul className="list-disc space-y-1 rounded-card border border-caution-rule bg-caution-bg py-4 pl-8 pr-4 text-sm text-ink-2">
                {forecast.confidence_reasons.map((r) => (
                  <li key={r}>{r}</li>
                ))}
                {forecast.caveats.map((c) => (
                  <li key={c}>{c}</li>
                ))}
              </ul>
            )}

            <Explain metrics={['forecast', 'forecast_range', 'forecast_error', 'forecast_confidence', 'forecast_method']}
                     notes={{
                       forecast: forecast.caveats,
                       forecast_range: [
                         `Interval method: ${forecast.interval.method}.`,
                         forecast.interval.measured_coverage !== null
                           ? `Measured coverage on the test weeks: ${pct(forecast.interval.measured_coverage * 100)}.`
                           : null,
                       ],
                       forecast_error: [
                         `Test weeks ${forecast.accuracy.test_weeks[0]}–${forecast.accuracy.test_weeks[1]}; pooled error ${pct(forecast.accuracy.pooled_wape, 2)}.`,
                       ],
                       forecast_confidence: forecast.confidence_reasons,
                       forecast_method: [summary?.deployment_rationale],
                     }} />
          </section>

          <Section
            title="History and forecast"
            description="Actual weekly revenue, with the forecast and its range drawn only for weeks the predictor never trained on: the held-out test weeks and next week."
          >
            <figure
                    aria-label={`Chart of actual weekly revenue for ${forecast.department}, with out-of-sample forecasts and their prediction interval. The forecast for week ${forecast.forecast_week} is ${fmtMoney(forecast.prediction)}, with a range of ${fmtMoney(forecast.lower_bound)} to ${fmtMoney(forecast.upper_bound)}. The same figures are in the table below.`}>
              <div className="h-64 sm:h-80">
                <ResponsiveContainer width="100%" height="100%">
                  <ComposedChart data={chart}
                                 margin={{ top: 16, right: 10, bottom: 5, left: 0 }}>
                    <CartesianGrid {...GRID_PROPS} />
                    <XAxis dataKey="week" tick={AXIS_TICK}
                           tickFormatter={(w) => `wk ${w}`} minTickGap={12} />
                    <YAxis tick={AXIS_TICK}
                           tickFormatter={(v) => fmtNum(v)} width={56} />
                    <Tooltip
                      {...TOOLTIP_STYLE}
                      formatter={(v: unknown, name: string) =>
                        Array.isArray(v)
                          ? [`${fmtMoney(v[0])} – ${fmtMoney(v[1])}`, 'range']
                          : [fmtMoney(v as number), name]}
                      labelFormatter={(l) => `week ${l}`} />
                    <Legend wrapperStyle={LEGEND_STYLE} />
                    <Area dataKey="band" name="forecast range"
                          stroke="none" fill={CHART.band} fillOpacity={1}
                          connectNulls={false} isAnimationActive={false} />
                    <Line dataKey="actual" name="actual" stroke={CHART.ink}
                          strokeWidth={2} dot={false} connectNulls isAnimationActive={false} />
                    <Line dataKey="forecast" name="forecast" stroke={CHART.model}
                          strokeWidth={2} strokeDasharray="5 4" dot={false}
                          connectNulls isAnimationActive={false} />
                    <ReferenceLine x={forecast.observed_until_week}
                                   stroke={CHART.context} strokeDasharray="2 2"
                                   /* insideTopRight, so the text ends at the line:
                                      the line sits one week from the right edge,
                                      and a label centred on it was cut off on a
                                      phone. */
                                   label={{ value: 'last observed', fontSize: 10,
                                            position: 'insideTopRight', fill: CHART.axis }} />
                  </ComposedChart>
                </ResponsiveContainer>
              </div>
            </figure>
            <div className="mt-4">
              <Disclosure summary="View these figures as a table">
                <div className="max-h-80 overflow-auto">
                  <table className="w-full text-sm">
                    <caption className="sr-only">
                      Actual revenue, forecast and forecast range for {forecast.department}, by week
                    </caption>
                    <thead className="text-left text-xs uppercase tracking-wide text-muted">
                      <tr>
                        <th scope="col" className="py-1 pr-4 font-medium">Week</th>
                        <th scope="col" className="py-1 pr-4 text-right font-medium">Actual</th>
                        <th scope="col" className="py-1 pr-4 text-right font-medium">Forecast</th>
                        <th scope="col" className="py-1 pr-4 text-right font-medium">Range</th>
                        <th scope="col" className="py-1 font-medium">Note</th>
                      </tr>
                    </thead>
                    <tbody>
                      {history.map((p) => (
                        <tr key={p.week_no} className="border-t border-rule text-ink-2">
                          <th scope="row" className="py-1 pr-4 text-left font-normal tabular-nums">{p.week_no}</th>
                          <td className="py-1 pr-4 text-right tabular-nums">{fmtMoney(p.actual)}</td>
                          <td className="py-1 pr-4 text-right tabular-nums">
                            {outOfSample(p) ? fmtMoney(p.prediction) : '—'}
                          </td>
                          <td className="whitespace-nowrap py-1 pr-4 text-right tabular-nums">
                            {outOfSample(p)
                              ? `${fmtMoney(p.lower_bound)} – ${fmtMoney(p.upper_bound)}` : '—'}
                          </td>
                          <td className="whitespace-nowrap py-1 text-xs text-muted">
                            {p.is_forecast ? 'next week' : p.split === 'test' ? 'held-out test week' : ''}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Disclosure>
            </div>
          </Section>

          <Section title="How this forecast is made">
            <div className="space-y-5">
              <p className="max-w-[65ch] text-base text-ink-2">
                {forecast.explanation.description}{' '}
                {summary && baselineRuns && (
                  <>
                    A machine-learning model was tested against this method on weeks{' '}
                    {summary.windows.test[0]}–{summary.windows.test[1]} and did not do
                    better, so the simpler method is the one that runs.{' '}
                  </>
                )}
                It cannot anticipate a promotion, a spike or a level shift.
              </p>

              {summary && deployed && (
                <dl className="grid grid-cols-1 gap-4 sm:grid-cols-3">
                  <Fact label={`${methodName(summary.model_type)} (in use)`}
                        value={pct(deployed.wape, 2)}
                        sub={`error across all departments, test weeks ${summary.windows.test[0]}–${summary.windows.test[1]}`} />
                  {challenger && (
                    <Fact label="Machine-learning model (tested)"
                          value={pct(challenger.wape, 2)}
                          sub={baselineRuns ? 'same weeks · did not beat the method in use' : 'same weeks'} />
                  )}
                  <Fact label={`${forecast.department}, method in use`}
                        value={forecast.accuracy.department_wape !== null
                          ? pct(forecast.accuracy.department_wape) : '—'}
                        sub="this department’s error on the same weeks" />
                </dl>
              )}

              <Disclosure summary={forecast.explanation.additive
                ? 'See the weeks that were averaged' : 'See what contributed to this number'}>
                <p className="mb-2 text-sm text-muted">
                  {forecast.explanation.additive
                    ? 'These figures reconcile to the forecast exactly.'
                    : 'These are approximate attributions and do not sum to the forecast.'}
                </p>
                <table className="w-full text-sm">
                  <caption className="sr-only">What the forecast was calculated from</caption>
                  <tbody>
                    {forecast.explanation.contributors.map((c) => (
                      <tr key={c.label} className="border-t border-rule">
                        <th scope="row" className="py-1.5 text-left font-normal text-ink-2">{c.label}</th>
                        <td className="py-1.5 text-right tabular-nums text-ink">
                          {c.value_usd !== undefined ? fmtMoney(c.value_usd) : '—'}
                        </td>
                        <td className="w-16 py-1.5 text-right text-xs text-muted">
                          {c.weight !== undefined
                            ? `× ${c.weight.toFixed(2)}` : ''}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </Disclosure>
            </div>
          </Section>

          {summary && (
            <Disclosure summary="Technical detail: every method tested, data windows and model version">
              <div className="space-y-6">
                <div>
                  <h3 className="font-medium text-ink">Every method on the held-out test weeks</h3>
                  <p className="mt-1 text-sm text-muted">
                    Weeks {summary.windows.test[0]}–{summary.windows.test[1]}, untouched
                    during model selection. Sorted by WAPE, lowest first.
                  </p>
                  <div className="mt-3 overflow-x-auto">
                    <table className="w-full text-sm">
                      <caption className="sr-only">Test error of every forecasting method</caption>
                      <thead className="text-xs uppercase tracking-wide text-muted">
                        <tr>
                          <th scope="col" className="pb-1 text-left font-medium">method</th>
                          <th scope="col" className="pb-1 text-right font-medium">MAE</th>
                          <th scope="col" className="pb-1 text-right font-medium">WAPE</th>
                        </tr>
                      </thead>
                      <tbody>
                        {baselines.map((b) => {
                          const isDeployed = b.name === summary.model_type;
                          return (
                            <tr key={b.name}
                                className={`border-t border-rule ${
                                  isDeployed ? 'font-semibold text-ink' : 'text-ink-2'}`}>
                              <th scope="row" className={`py-1.5 text-left ${isDeployed ? '' : 'font-normal'}`}>
                                {b.name === CHALLENGER ? 'ML challenger' : b.name.replace(/_/g, ' ')}
                                {isDeployed && (
                                  <span className="ml-2 rounded bg-ink px-1.5 py-0.5 text-[10px] uppercase text-paper">
                                    in use
                                  </span>
                                )}
                              </th>
                              <td className="py-1.5 text-right tabular-nums">
                                {fmtNum(b.mae, 0)}
                              </td>
                              <td className="py-1.5 text-right tabular-nums">
                                {pct(b.wape, 2)}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                  <p className="mt-3 text-xs text-muted">
                    Pooled WAPE {pct(summary.metrics.test.scores.wape, 2)} is
                    dollar-weighted and GROCERY is half the revenue. Weighting every
                    department equally gives{' '}
                    {pct(summary.metrics.macro_wape_test)}, which is the
                    figure that describes the system rather than its largest
                    department.
                  </p>
                </div>

                <div>
                  <h3 className="font-medium text-ink">Why this method runs</h3>
                  <p className="mt-1 max-w-[70ch] text-sm text-ink-2">{summary.deployment_rationale}</p>
                </div>

                <div>
                  <h3 className="font-medium text-ink">Data windows</h3>
                  <p className="mt-1 max-w-[70ch] text-sm text-ink-2">
                    Trained on weeks {summary.windows.train[0]}–
                    {summary.windows.train[1]}; intervals calibrated on weeks{' '}
                    {summary.windows.validation[0]}–{summary.windows.validation[1]};
                    tested on weeks {summary.windows.test[0]}–{summary.windows.test[1]},
                    which were locked during model selection. Leakage audit:{' '}
                    {summary.leakage_audit_passed ? 'passed' : 'FAILED'}. Weeks 1 and
                    102 are partial (5 and 6 days); no feature reads a week before 20,
                    because 99.7% of the panel had been recruited by then and earlier
                    weeks are enrolment rather than demand.
                  </p>
                  <p className="mt-2 break-words font-mono text-2xs text-muted">
                    model {forecast.model_version}
                  </p>
                </div>
              </div>
            </Disclosure>
          )}
        </>
      )}

      <Workflow current="predict" heading="Where next" />
    </div>
  );
}
