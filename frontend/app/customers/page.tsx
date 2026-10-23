'use client';

/**
 * Customers -- who the revenue comes from, and how well the panel keeps them.
 *
 * Two questions a retailer asks about its base: which segments carry the
 * revenue (RFM), and whether households stay (retention by onboarding group).
 * Both read existing published aggregates through the read-only API; nothing
 * here computes a figure, and the "Explain" panels are static definitions.
 *
 * This is a descriptive view. It sits beside the Overview and does not duplicate
 * it: the Overview trends revenue over time; this profiles the customer base.
 */

import { useEffect, useState } from 'react';
import { CaveatBar } from '@/components/Caveats';
import { RetentionCurves, RfmBehaviourTable, type RetentionRow, type Segment } from '@/components/customers';
import { Explain } from '@/components/explain';
import { SegmentShare } from '@/components/overview';
import { Callout, EmptyState, ErrorState, PageHeader, Section, Skeleton, Stat } from '@/components/ui';
import { get } from '@/lib/api';
import { retentionAt, topSegment } from '@/lib/customers.mjs';

/** One GET with its own loading and error state, so a slow or failed section
 *  never blanks the rest of the page. (Mirrors the Overview's hook.) */
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

const RETENTION_MONTH = 12;   // where cohorts are still reasonably complete

export default function CustomersPage() {
  const [attempt, setAttempt] = useState(0);
  const retry = () => setAttempt((n) => n + 1);

  const rfm = useApi<{ items: Segment[] }>('/api/v1/segments/rfm', attempt);
  const retention = useApi<{ items: RetentionRow[]; basis?: string }>('/api/v1/retention/tenure', attempt);

  const segments = rfm.data?.items ?? [];
  const top = topSegment(segments);
  const retentionRows = retention.data?.items ?? [];
  const at12 = retentionAt(retentionRows, RETENTION_MONTH);

  return (
    <div className="max-w-5xl space-y-8">
      <PageHeader eyebrow="Who the customers are" title="Customer analytics">
        Who the panel&rsquo;s revenue comes from, and how well it keeps them. Every figure is
        computed by PostgreSQL from the published aggregates and read through the read-only API.
      </PageHeader>

      {/* --- RFM segments --- */}
      <Section eyebrow="Segments" title="Who carries the revenue"
        description="Households grouped by how recently, how often and how much they buy (RFM), with the purchasing behaviour behind each group.">
        {rfm.error != null && <ErrorState error={rfm.error} what="the customer segments" onRetry={retry} />}
        {rfm.loading && rfm.error == null && <Skeleton label="the customer segments" className="h-56" />}
        {!rfm.loading && rfm.error == null && segments.length === 0 && (
          <EmptyState title="No segments returned">The RFM endpoint returned no rows.</EmptyState>
        )}
        {segments.length > 0 && (
          <div className="space-y-5">
            {top && (
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
                <Stat label="Leading segment" value={top.segment} emphasis
                      sub={`${top.pctRevenue}% of revenue`} />
                <Stat label="Share of households" value={`${top.pctHouseholds}%`}
                      sub="of the 2,500-household panel" />
                <Stat label="Revenue concentration" value={`${(top.pctRevenue / top.pctHouseholds).toFixed(1)}×`}
                      sub="revenue share ÷ household share" />
              </div>
            )}
            {top && (
              <p className="max-w-[65ch] font-display text-xl leading-snug text-ink">
                {top.segment} are {top.pctHouseholds}% of households but {top.pctRevenue}% of revenue
                &mdash; the base is concentrated, so retaining this group matters more than its size suggests.
              </p>
            )}
            <SegmentShare segments={segments} />
            <RfmBehaviourTable segments={segments} />
            <Explain metrics={['rfm_segments']} />
          </div>
        )}
      </Section>

      {/* --- Retention --- */}
      <Section eyebrow="Retention" title="Do households stay?"
        description="The share of each onboarding group still buying over relative tenure — each household timed from its own first purchase, not a calendar date.">
        {retention.error != null && <ErrorState error={retention.error} what="the retention curves" onRetry={retry} />}
        {retention.loading && retention.error == null && <Skeleton label="the retention curves" className="h-72" />}
        {!retention.loading && retention.error == null && retentionRows.length === 0 && (
          <EmptyState title="No retention data returned">The retention endpoint returned no rows.</EmptyState>
        )}
        {retentionRows.length > 0 && (
          <div className="space-y-5">
            {at12.length > 0 && (
              <p className="max-w-[65ch] text-sm text-ink-2">
                At {RETENTION_MONTH} months&rsquo; tenure, {at12.map((x, i) => (
                  <span key={x.segment}>
                    {i > 0 && (i === at12.length - 1 ? ' and ' : ', ')}
                    <span className="font-medium text-ink">{x.pct}%</span> of the{' '}
                    {x.segment.replace(' first basket', '').toLowerCase()}-first-basket group
                  </span>
                ))} were still active &mdash; first-basket size barely separates them, so a bigger opening
                trip does not by itself buy loyalty here.
              </p>
            )}
            <RetentionCurves rows={retentionRows} />
            <Callout tone="neutral" title="Reading the tail">
              The groups start at 100% in month 0 by construction. Past about month 12 the curves fall
              because the 711-day window gives later tenure months fewer households to observe &mdash; that
              is incomplete follow-up, not churn.
            </Callout>
            <Explain metrics={['retention']}
                     notes={{ retention: [retention.data?.basis] }} />
          </div>
        )}
      </Section>

      <CaveatBar collapsible panel revenue />
    </div>
  );
}
