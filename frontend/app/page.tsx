'use client';

/**
 * Overview -- the product's home.
 *
 * The page is the workflow: Detect (what changed) → Explain (what is behind
 * it) → Predict (what is likely next) → Investigate (could a campaign have
 * caused it). Each step is a section with one figure worth acting on and a
 * link into the engine that goes deeper.
 *
 * Every number is a value an endpoint returned. Nothing is computed here
 * beyond picking rows (the highest week, the latest full week), and the
 * "Explain" panels are static definitions, never generated text.
 */

import { useEffect, useId, useState } from 'react';
import { CaveatBar } from '@/components/Caveats';
import { Explain } from '@/components/explain';
import {
  CampaignSummary, ForecastSummary, SegmentShare, WeeklyChart, WeeklyTable,
  type Campaign, type Flagged, type Forecast, type Segment, type Weekly,
} from '@/components/overview';
import {
  ActionLink, Callout, ErrorState, PageHeader, Section, Skeleton, Stat,
} from '@/components/ui';
import { Workflow } from '@/components/workflow';
import { fmtMoney, fmtNum, get } from '@/lib/api';
import { EXAMPLES } from '@/lib/examples';
import {
  ENROLMENT_FLOOR_WEEK, STEPS, askHref, driversQuestion, flaggedSummary, forecastDepartment,
  latestFullWeek, peakWeek,
} from '@/lib/overview.mjs';

type Totals = {
  total_revenue: number; total_baskets: number; total_households: number;
  avg_basket_value: number; total_units: number; weeks_covered: number;
  window_basis?: string;
};

// The campaign the Causal page leads with: the least contaminated one.
const CAMPAIGN_ID = 26;

/** One GET with its own loading and error state, so a slow or failed section
 *  never blanks the rest of the page. Earlier data stays up while a refetch
 *  (a new department) is in flight. */
function useApi<T>(path: string, attempt: number) {
  const [state, setState] = useState<{ data: T | null; error: unknown; loading: boolean }>(
    { data: null, error: null, loading: true });
  useEffect(() => {
    let live = true;
    setState((s) => ({ ...s, loading: true }));
    get<T>(path)
      .then((data) => { if (live) setState({ data, error: null, loading: false }); })
      .catch((error) => { if (live) setState({ data: null, error, loading: false }); });
    return () => { live = false; };
  }, [path, attempt]);
  return state;
}

const stepLabel = (key: string) => {
  const i = STEPS.findIndex((s) => s.key === key);
  return { eyebrow: `${i + 1} · ${STEPS[i].verb}`, title: STEPS[i].question };
};

const verified = (benchmark: string) => EXAMPLES.find((e) => e.benchmark === benchmark)?.question;

function Fact({ label, value, sub }: { label: string; value: string; sub: React.ReactNode }) {
  return (
    <div className="border-l-2 border-rule pl-3">
      <dt className="text-xs uppercase tracking-wide text-muted">{label}</dt>
      <dd className="mt-0.5 text-xl font-semibold tabular-nums text-ink">{value}</dd>
      <dd className="text-xs text-muted">{sub}</dd>
    </div>
  );
}

export default function OverviewPage() {
  const id = useId();
  const [dept, setDept] = useState('');
  const [attempt, setAttempt] = useState(0);
  const retry = () => setAttempt((n) => n + 1);
  const q = dept ? `?department=${encodeURIComponent(dept)}` : '';

  // The department filter re-queries the server, so filtered figures are
  // computed by SQL, never by trimming rows in the browser.
  const totals = useApi<Totals>(`/api/v1/overview${q}`, attempt);
  const weekly = useApi<{ items: Weekly[] }>(`/api/v1/revenue/weekly${q}`, attempt);
  const departments = useApi<{ items: { department: string }[] }>('/api/v1/departments', attempt);
  const segments = useApi<{ items: Segment[] }>('/api/v1/segments/rfm', attempt);
  const anomalies = useApi<{ items: Flagged[]; note?: string; z_threshold: number }>('/api/v1/ai/anomalies', attempt);
  const servable = useApi<{ items: { department: string; servable: boolean }[] }>(
    '/api/v1/forecast/departments', attempt);
  const fcDept = forecastDepartment(
    dept, (servable.data?.items ?? []).filter((d) => d.servable).map((d) => d.department));
  const forecast = useApi<Forecast>(
    `/api/v1/forecast?department=${encodeURIComponent(fcDept)}`, attempt);
  const campaign = useApi<Campaign>(`/api/v1/causal/analysis/${CAMPAIGN_ID}`, attempt);

  const weeks = weekly.data?.items ?? [];
  const peak = peakWeek(weeks);
  const latest = latestFullWeek(weeks);
  // The detector runs on all departments together, so its flags are only
  // drawn on the all-department series.
  const flagged = dept ? [] : anomalies.data?.items ?? [];
  const flags = flaggedSummary(flagged);
  const top = segments.data?.items[0];
  const detect = stepLabel('detect');
  const explain = stepLabel('explain');
  const predict = stepLabel('predict');
  const investigate = stepLabel('investigate');
  const segmentQuestion = verified('pub-07');
  const departmentQuestion = verified('pub-02');

  return (
    <div className="space-y-8">
      <PageHeader eyebrow="Detect → Explain → Predict → Investigate"
                  title="What changed, what is likely next, and what caused it">
        Two years of grocery purchases from 2,500 households, turned into answers you can check.
        PostgreSQL and tested statistical models compute every figure. A language model only
        drafts SQL; it never produces a number.
      </PageHeader>

      <Workflow />

      <section aria-labelledby={`${id}-glance`} className="space-y-4">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 id={`${id}-glance`} className="font-display text-lg leading-tight text-ink">
              The panel at a glance
            </h2>
            <p className="mt-1 text-sm text-muted">
              {dept ? `Filtered to ${dept}` : 'All departments'}
              {totals.data ? ` · ${totals.data.weeks_covered} weeks` : ''}
            </p>
          </div>
          <label className="flex items-center gap-2 text-sm text-ink-2">
            Department
            <select value={dept} onChange={(e) => setDept(e.target.value)}
              className="min-h-control max-w-[14rem] rounded-input border border-rule bg-paper px-3 text-sm text-ink">
              <option value="">All departments</option>
              {(departments.data?.items ?? []).map((d) => (
                <option key={d.department} value={d.department}>{d.department}</option>
              ))}
            </select>
          </label>
        </div>

        {totals.error != null && <ErrorState error={totals.error} what="the totals" onRetry={retry} />}
        {departments.error != null && (
          <ErrorState error={departments.error} what="the department list" onRetry={retry} />
        )}
        {totals.data ? (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-5">
            <Stat label="Revenue" value={fmtMoney(totals.data.total_revenue)} sub="net of retailer discounts" />
            <Stat label="Baskets" value={fmtNum(totals.data.total_baskets)} sub="shopping trips" />
            <Stat label="Households" value={fmtNum(totals.data.total_households)} sub="made a purchase" />
            <Stat label="Average basket" value={fmtMoney(totals.data.avg_basket_value)} sub="revenue per trip" />
            <Stat label="Units" value={fmtNum(totals.data.total_units)} sub="excludes goods sold by weight" />
          </div>
        ) : totals.error == null && <Skeleton label="the totals" className="h-24" />}
        <Explain metrics={['revenue', 'baskets', 'households', 'avg_basket', 'units']}
                 notes={{ households: [totals.data?.window_basis] }} />
      </section>

      <Section id="what-changed" eyebrow={detect.eyebrow} title={detect.title}
        description={`Revenue by week${dept ? ` for ${dept}` : ''}. The early rise is households joining the panel, so it is marked and should not be read as growth.`}>
        {weekly.error != null && <ErrorState error={weekly.error} what="weekly revenue" onRetry={retry} />}
        {weeks.length === 0 && weekly.error == null && <Skeleton label="weekly revenue" className="h-72" />}
        {weeks.length > 0 && (
          <div className="space-y-5">
            <dl className="grid grid-cols-2 gap-4 sm:grid-cols-3">
              {peak && (
                <Fact label="Busiest full week" value={fmtMoney(peak.revenue)}
                      sub={`Week ${peak.week_no}, starting ${peak.start_date}`} />
              )}
              {latest && (
                <Fact label="Latest full week" value={fmtMoney(latest.revenue)}
                      sub={`Week ${latest.week_no} · 7-week average ${fmtMoney(latest.rolling_7wk_avg)}`} />
              )}
              <Fact label="Flagged by the detector"
                    value={dept ? '—' : anomalies.data ? `${flags.count} week${flags.count === 1 ? '' : 's'}` : '…'}
                    sub={dept ? 'It runs on all departments together'
                      : anomalies.error != null ? 'Could not be loaded'
                      : `Revenue at least ${anomalies.data?.z_threshold ?? '…'} standard deviations from the mean`} />
            </dl>

            <WeeklyChart weekly={weeks} flagged={flagged} floor={ENROLMENT_FLOOR_WEEK} department={dept} />

            {!dept && anomalies.data && (
              <Callout tone={flags.count > 0 ? 'caution' : 'neutral'} title={flags.headline}>
                {flags.detail}
              </Callout>
            )}
            {!dept && anomalies.error != null && (
              <ErrorState error={anomalies.error} what="the flagged weeks" onRetry={retry} />
            )}

            <div className="flex flex-wrap gap-3">
              {peak && !dept ? (
                <ActionLink primary href={askHref(driversQuestion(peak.week_no))}>
                  Ask what drove week {peak.week_no}
                </ActionLink>
              ) : (
                <ActionLink primary href="/query">Ask about this</ActionLink>
              )}
            </div>

            <WeeklyTable weekly={weeks} flagged={flagged} floor={ENROLMENT_FLOOR_WEEK} />
            <Explain metrics={['weekly_revenue', 'flagged_weeks']}
                     notes={{ flagged_weeks: [anomalies.data?.note] }} />
          </div>
        )}
      </Section>

      <Section eyebrow={explain.eyebrow} title={explain.title}
        description="Who the revenue comes from. Any question about it goes to Ask, where PostgreSQL computes the answer and shows its working.">
        {segments.error != null && <ErrorState error={segments.error} what="the customer segments" onRetry={retry} />}
        {!segments.data && segments.error == null && <Skeleton label="the customer segments" className="h-56" />}
        {segments.data && top && (
          <div className="space-y-5">
            <p className="max-w-[65ch] font-display text-xl leading-snug text-ink">
              {top.segment} are {top.pct_of_panel}% of households and {top.pct_of_revenue}% of revenue.
            </p>
            <SegmentShare segments={segments.data.items} />
            <div className="flex flex-wrap gap-3">
              {segmentQuestion && (
                <ActionLink primary href={askHref(segmentQuestion)}>Ask which segment leads</ActionLink>
              )}
              {departmentQuestion && (
                <ActionLink href={askHref(departmentQuestion)}>Ask which departments earn most</ActionLink>
              )}
            </div>
            <Explain metrics={['rfm_segments']} />
          </div>
        )}
      </Section>

      <div className="grid gap-6 lg:grid-cols-2">
        <Section eyebrow={predict.eyebrow} title={predict.title}>
          {forecast.error != null && <ErrorState error={forecast.error} what="the forecast" onRetry={retry} />}
          {!forecast.data && forecast.error == null && <Skeleton label="the forecast" className="h-56" />}
          {forecast.data && (
            <div className="space-y-5">
              {dept && fcDept !== dept && (
                <p className="text-sm text-muted">No forecast is served for {dept}; showing {fcDept}.</p>
              )}
              <ForecastSummary f={forecast.data} />
              <ActionLink primary href={`/forecast?department=${encodeURIComponent(fcDept)}`}>
                Open the forecast
              </ActionLink>
              <Explain metrics={['forecast']}
                       notes={{ forecast: [...forecast.data.caveats, `Interval method: ${forecast.data.interval.method}.`] }} />
            </div>
          )}
        </Section>

        <Section eyebrow={investigate.eyebrow} title={investigate.title}>
          {campaign.error != null && <ErrorState error={campaign.error} what="the campaign estimate" onRetry={retry} />}
          {!campaign.data && campaign.error == null && <Skeleton label="the campaign estimate" className="h-56" />}
          {campaign.data && (
            <div className="space-y-5">
              <CampaignSummary a={campaign.data} />
              <ActionLink primary href="/causal">Investigate campaigns</ActionLink>
              <Explain metrics={['campaign_effect']}
                       notes={{ campaign_effect: [campaign.data.parallel_trends.verdict, ...campaign.data.warnings] }} />
            </div>
          )}
        </Section>
      </div>

      <CaveatBar collapsible calendar panel revenue />
    </div>
  );
}
