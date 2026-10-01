/**
 * Data-basis caveats rendered in the UI, not just the docs.
 *
 * These are not decoration. The panel structure and the weekday convention both
 * constrain what the figures on screen mean, and a chart rendered without them
 * invites a claim the data does not support. A screenshot of this dashboard
 * should carry its own caveats.
 */

import { Disclosure } from '@/components/ui';

export function CalendarCaveat() {
  return (
    <p className="text-xs leading-relaxed text-muted">
      <span className="font-semibold text-ink-2">Calendar basis:</span>{' '}
      the source records day 1–711 with no published start date.{' '}
      <span className="font-medium">Day 1 is anchored to Wednesday 2015-01-07</span>{' '}
      so day 6 falls on a Monday, matching the source <code>WEEK_NO</code> rule{' '}
      <code>(day + 8) / 7</code>. Elapsed intervals, month boundaries and
      year-over-year comparisons are real;{' '}
      <span className="font-medium">weekday labels are a modelling convention</span>{' '}
      and carry no meaning.
    </p>
  );
}

export function PanelCaveat() {
  return (
    <p className="text-xs leading-relaxed text-muted">
      <span className="font-semibold text-ink-2">Panel basis:</span>{' '}
      this is a household panel, not an acquisition funnel. 99.8% of households
      make their first purchase within 180 days of a 711-day window (median day
      69), so calendar-month cohorts would contrast early recruits against six
      stragglers. Retention is reported on{' '}
      <span className="font-medium">relative tenure</span> — each household on
      its own timeline from its first purchase.
    </p>
  );
}

export function RevenueCaveat() {
  return (
    <p className="text-xs leading-relaxed text-muted">
      <span className="font-semibold text-ink-2">Revenue basis:</span>{' '}
      <code>sales_value</code> is the net amount charged.{' '}
      <code>gross_value = sales_value − retail_disc</code>, where{' '}
      <code>retail_disc</code> is stored negative. Returns appear as
      quantity ≤ 0, never as negative money. 23,101 rows record weighted goods
      in grams and are excluded from unit counts.
    </p>
  );
}

export function CaveatBar({
  calendar = true,
  panel = false,
  revenue = false,
  collapsible = false,
}: {
  calendar?: boolean;
  panel?: boolean;
  revenue?: boolean;
  /** Behind a disclosure, for a page whose sections already state their own
   *  limits inline (the Overview). The analysis pages keep it open. */
  collapsible?: boolean;
}) {
  const caveats = (
    <>
      {calendar && <CalendarCaveat />}
      {panel && <PanelCaveat />}
      {revenue && <RevenueCaveat />}
    </>
  );
  if (collapsible) {
    return (
      <Disclosure summary="What these numbers mean: calendar, panel and revenue basis">
        <div className="space-y-2">{caveats}</div>
      </Disclosure>
    );
  }
  return (
    <aside className="space-y-2 rounded-card border border-caution-rule bg-caution-bg p-4">
      {/* An <h2>: this is a region of the page in its own right, not a
          subsection of whichever section happens to precede it. */}
      <h2 className="text-xs font-semibold uppercase tracking-wide text-caution">
        What these numbers mean
      </h2>
      {caveats}
    </aside>
  );
}
