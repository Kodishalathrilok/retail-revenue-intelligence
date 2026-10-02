'use client';

/**
 * Causal view: the estimate, its interval and a plain-English verdict first,
 * then what the estimate assumes, then the pre-campaign evidence.
 *
 * Two comparisons are kept deliberately:
 *   1. the naive before/after next to the DiD estimate, because the gap between
 *      them is the point;
 *   2. campaign 26 (6.6% contaminated) next to campaign 18 (90.8%), because a
 *      contaminated estimate beside a clean one demonstrates what contamination
 *      does in a way a caveat cannot.
 *
 * Wording this page must keep (lib/overview.test.mjs and
 * tests/test_parallel_trends_wording.py): a pre-trend test that passes is
 * "not rejected", never a finding that the trends are parallel, and an
 * interval that includes zero is never reported as the absence of an effect.
 * The verdict and assumption text is deterministic; no model writes it. The
 * API's stored pre-trend verdict string is not rendered anywhere: the page's
 * own pretrendSentence is the single wording.
 */

import { useEffect, useId, useState, type ReactNode } from 'react';
import {
  CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { CaveatBar } from '@/components/Caveats';
import {
  AXIS_TICK, CHART, GRID_PROPS, LEGEND_STYLE, TOOLTIP_STYLE,
} from '@/components/chart-theme';
import { Explain } from '@/components/explain';
import {
  Badge, Callout, Disclosure, ErrorState, Fact, FactRow, PageHeader, Skeleton, Stat, type Tone,
} from '@/components/ui';
import { Workflow } from '@/components/workflow';
import { fmtNum, get } from '@/lib/api';
import {
  campaignVerdict, displayWarning, effectSummary, pretrendSentence, signedMoney,
} from '@/lib/overview.mjs';

type Analysis = {
  campaign_id: number; treated_n: number; control_n: number; contaminated_pct: number;
  naive_difference: number; did_estimate: number; did_stderr: number; did_pvalue: number;
  ci_low: number; ci_high: number; adjusted_estimate: number | null;
  adjusted_stderr: number | null; confounders_used: string[];
  parallel_trends: {
    passed: boolean; treated_slope: number; control_slope: number;
    interaction_pvalue: number; pre_weeks: number;
  };
  pre_period_series: {
    treated: { week_no: number; mean_spend: number }[];
    control: { week_no: number; mean_spend: number }[];
  };
  confidence: string; warnings: string[]; precomputed?: boolean;
};

const VERDICT_TONE: Record<string, Tone> = {
  CREDIBLE: 'positive',
  WEAK: 'caution',
  'NOT CREDIBLE': 'negative',
};

const usd2 = (v: number) => `$${v.toFixed(2)}`;

/** What the estimate rests on. Static text; only the figures the API reported
 *  for this campaign are filled in. */
function Assumptions({ a }: { a: Analysis }) {
  return (
    <div>
      <h3 className="text-sm font-semibold text-ink">Assumptions and limitations</h3>
      <dl className="mt-1">
        <FactRow term="Parallel trends">
          The estimate assumes that, without the campaign, weekly spend in the two groups
          would have moved in parallel. That cannot be observed; the pre-trend test only
          checks the weeks before the campaign.
          <span className="mt-1 block font-medium text-ink">{pretrendSentence(a.parallel_trends)}</span>
        </FactRow>
        <FactRow term="No other campaign">
          Households that were in an overlapping campaign during the window are left out of
          both groups. {a.contaminated_pct}% of this campaign’s enrolled households were in one.
        </FactRow>
        <FactRow term="Targeted, not randomised">
          The retailer chose which households received the campaign. Those households may
          differ from the rest in ways the comparison does not capture, so this is an
          estimate, not proof of cause.
        </FactRow>
        <FactRow term="What the interval covers">
          Sampling uncertainty only. It does not widen to allow for a failed assumption, and
          an interval that includes zero does not show there was no effect.
        </FactRow>
        <FactRow term="Scope">
          One campaign, in a panel of 2,500 households. The estimate describes these
          households, not shoppers in general.
          {a.precomputed && ' On this published demo it was computed when the data was published and is not re-estimated live.'}
        </FactRow>
      </dl>
    </div>
  );
}

function AnalysisPanel({ a, title, intro, lead = false }: {
  a: Analysis; title: string; intro?: ReactNode;
  /** The campaign the page leads with keeps its assumptions and its
   *  pre-campaign chart open. */
  lead?: boolean;
}) {
  const id = useId();
  const pt = a.parallel_trends;
  const verdict = campaignVerdict(a);
  const merged = a.pre_period_series.treated.map((t) => ({
    week: t.week_no,
    treated: t.mean_spend,
    control: a.pre_period_series.control.find((c) => c.week_no === t.week_no)?.mean_spend ?? null,
  }));
  const chartSummary = `Line chart of mean weekly spend per household over the `
    + `${pt.pre_weeks} weeks before campaign ${a.campaign_id}, campaign households against `
    + `comparison households. Slopes: campaign group ${pt.treated_slope.toFixed(4)}, comparison `
    + `group ${pt.control_slope.toFixed(4)}. The same figures are in the table below.`;

  return (
    <section aria-labelledby={`${id}-title`}
             className="space-y-5 rounded-card border border-rule bg-paper-2 p-4 sm:p-5">
      <div>
        <div className="flex flex-wrap items-center gap-3">
          <h2 id={`${id}-title`} className="font-display text-lg leading-tight text-ink">{title}</h2>
          <Badge tone={VERDICT_TONE[a.confidence] ?? 'neutral'}>Evidence: {a.confidence}</Badge>
        </div>
        {intro}
        <p className="mt-2 text-xs text-muted">
          {fmtNum(a.treated_n)} campaign households compared with {fmtNum(a.control_n)} others ·{' '}
          <span className={a.contaminated_pct > 25 ? 'font-semibold text-negative' : ''}>
            {a.contaminated_pct}% of enrolled households were also in an overlapping campaign
          </span>
        </p>
      </div>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        <Stat
          emphasis
          label="Estimated effect on weekly spend"
          value={signedMoney(a.did_estimate)}
          sub="per household per week · difference-in-differences"
        />
        <Stat
          label="95% confidence interval"
          value={`${signedMoney(a.ci_low)} to ${signedMoney(a.ci_high)}`}
          sub={`p = ${a.did_pvalue.toFixed(3)}`}
        />
        <Stat
          label="Standard error"
          value={usd2(a.did_stderr)}
          sub="clustered by household"
        />
      </div>

      <Callout tone={VERDICT_TONE[a.confidence] ?? 'neutral'} title={verdict.headline}>
        <p>{verdict.detail}</p>
        <p className="mt-2">{effectSummary(a).sentence}</p>
      </Callout>

      <Explain metrics={['campaign_effect', 'campaign_interval', 'campaign_stderr', 'pretrend_test']}
               notes={{ campaign_effect: a.warnings.map(displayWarning) }} />

      <div>
        <h3 className="text-sm font-semibold text-ink">How the estimate compares</h3>
        <dl className="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-3">
          <Fact label="Simple before/after"
                value={signedMoney(a.naive_difference)}
                sub="Campaign households only, with no comparison group. It absorbs every seasonal change." />
          <Fact label="Difference-in-differences"
                value={signedMoney(a.did_estimate)}
                sub="The campaign group’s change minus the comparison group’s change. The estimate above." />
          <Fact label="Adjusted for household differences"
                value={a.adjusted_estimate === null ? '—' : signedMoney(a.adjusted_estimate)}
                sub={a.confounders_used.length > 0
                  ? `Controls for ${a.confounders_used.map((c) => c.replace(/_/g, ' ')).join(', ')}`
                    + (a.adjusted_stderr !== null ? ` · standard error ${usd2(a.adjusted_stderr)}` : '')
                  : 'No adjustment was made.'} />
        </dl>
      </div>

      {/* Open for the campaign the page leads with. The contaminated one
          repeats the same assumptions, so there they are one tap away and its
          verdict and warnings stay in view. */}
      {lead ? <Assumptions a={a} /> : (
        <Disclosure summary="Assumptions and limitations for this estimate">
          <Assumptions a={a} />
        </Disclosure>
      )}

      {a.warnings.length > 0 && (
        <Callout tone="caution" title="Reported by the analysis">
          <ul className="list-disc space-y-1.5 pl-4">
            {a.warnings.map((w) => <li key={w}>{displayWarning(w)}</li>)}
          </ul>
        </Callout>
      )}

      <div>
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-sm font-semibold text-ink">Pre-trend test</h3>
          {/* "Not rejected", never "hold", and not painted as a pass: on this
              low-power test it is absence of evidence of a pre-trend. */}
          <Badge tone={pt.passed ? 'neutral' : 'negative'}>
            {pt.passed ? 'NOT REJECTED' : 'VIOLATED'}
          </Badge>
          <span className="text-xs text-ink-2">
            interaction p = {pt.interaction_pvalue.toFixed(4)} · {pt.pre_weeks} pre-campaign weeks
          </span>
        </div>
        <div className="mt-3">
          <Disclosure defaultOpen={lead} summary="Spending before the campaign: chart and figures">
            <p className="mb-2 text-xs text-muted">
              Mean weekly spend per household BEFORE the campaign. If these diverge,
              difference-in-differences attributes that divergence to the campaign.
            </p>
            <figure aria-label={chartSummary}>
              <div className="h-52 sm:h-56">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={merged} margin={{ top: 5, right: 10, bottom: 0, left: 0 }}>
                    <CartesianGrid {...GRID_PROPS} />
                    <XAxis dataKey="week" tick={AXIS_TICK} />
                    <YAxis tick={AXIS_TICK} width={44} tickFormatter={(v: number) => `$${v}`} />
                    <Tooltip {...TOOLTIP_STYLE} labelFormatter={(l) => `week ${l}`}
                             formatter={(v: number, name: string) => [usd2(v), name]} />
                    <Legend wrapperStyle={LEGEND_STYLE} />
                    <Line type="monotone" dataKey="treated" stroke={CHART.compare} dot={false}
                          name="campaign households" isAnimationActive={false} />
                    <Line type="monotone" dataKey="control" stroke={CHART.ink} dot={false}
                          name="comparison households" isAnimationActive={false} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </figure>
            <p className="mt-1 text-xs text-muted">
              slope per week — campaign group {pt.treated_slope.toFixed(4)} ·
              comparison group {pt.control_slope.toFixed(4)}
            </p>
            <div className="mt-3">
              <Disclosure summary="View these figures as a table">
              <div className="max-h-64 overflow-auto">
              <table className="w-full text-sm">
                <caption className="sr-only">
                  Mean weekly spend per household before campaign {a.campaign_id}, by week
                </caption>
                <thead className="text-left text-xs uppercase tracking-wide text-muted">
                  <tr>
                    <th scope="col" className="py-1 pr-4 font-medium">Week</th>
                    <th scope="col" className="py-1 pr-4 text-right font-medium">Campaign households</th>
                    <th scope="col" className="py-1 text-right font-medium">Comparison households</th>
                  </tr>
                </thead>
                <tbody>
                  {merged.map((r) => (
                    <tr key={r.week} className="border-t border-rule text-ink-2">
                      <th scope="row" className="py-1 pr-4 text-left font-normal tabular-nums">{r.week}</th>
                      <td className="py-1 pr-4 text-right tabular-nums">{usd2(r.treated)}</td>
                      <td className="py-1 text-right tabular-nums">
                        {r.control === null ? '—' : usd2(r.control)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              </div>
              </Disclosure>
            </div>
          </Disclosure>
        </div>
      </div>
    </section>
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
      <PageHeader eyebrow="Investigate · campaign effects" title="Did the campaign change spending?">
        What a campaign changed in weekly household spend: the estimate, its
        uncertainty and what it assumes. SQL builds the comparison and a
        difference-in-differences model estimates the effect — no language model
        produces an estimate.
      </PageHeader>

      {err != null && <ErrorState error={err} what="campaign 26" onRetry={retry} />}

      {clean ? <AnalysisPanel lead a={clean} title={`Campaign ${clean.campaign_id}`} /> : err == null && (
        <Skeleton label="the campaign analysis" className="h-96" />
      )}

      {stress && (
        <AnalysisPanel
          a={stress}
          title={`Contaminated comparison: campaign ${stress.campaign_id}`}
          intro={(
            <p className="mt-2 max-w-[70ch] text-sm text-ink-2">
              Campaign 18 is the largest campaign in the dataset and one of the
              worst candidates: 90.8% of its enrolled households were
              simultaneously in an overlapping campaign. It is shown here
              deliberately. Sorting campaigns by enrolment — the obvious choice —
              puts this one first.
            </p>
          )}
        />
      )}

      {stressErr != null && (
        <ErrorState error={stressErr} what="the contaminated comparison (campaign 18)"
                    onRetry={retry} />
      )}

      <CaveatBar collapsible calendar panel revenue />

      <Workflow current="investigate" heading="Where next" />
    </div>
  );
}
