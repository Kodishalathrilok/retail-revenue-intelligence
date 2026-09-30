/**
 * Shared UI primitives, painted only from the Almanac tokens (app/tokens.css).
 *
 * The four pages used to define their own Stat card three times over and paint
 * status in raw Tailwind emerald/amber/red. Everything here reads the token
 * layer instead, so a page composed from these parts cannot drift from the
 * shell it sits in.
 *
 * Status is never colour alone: every tone is rendered with a text label by the
 * caller, and these components only add the colour family.
 */

import type { ReactNode } from 'react';
import { publicMessage, technicalDetail } from '@/lib/api';

export type Tone = 'positive' | 'caution' | 'negative' | 'neutral' | 'accent';

const TONE: Record<Tone, { text: string; bg: string; rule: string; solid: string }> = {
  positive: { text: 'text-positive', bg: 'bg-positive-bg', rule: 'border-positive-rule', solid: 'bg-positive text-paper' },
  caution: { text: 'text-caution', bg: 'bg-caution-bg', rule: 'border-caution-rule', solid: 'bg-caution text-paper' },
  negative: { text: 'text-negative', bg: 'bg-negative-bg', rule: 'border-negative-rule', solid: 'bg-negative text-paper' },
  neutral: { text: 'text-ink-2', bg: 'bg-paper-3', rule: 'border-rule', solid: 'bg-ink-2 text-paper' },
  accent: { text: 'text-accent', bg: 'bg-paper-3', rule: 'border-rule', solid: 'bg-accent text-accent-ink' },
};

export function Card({ children, className = '' }: { children: ReactNode; className?: string }) {
  return (
    <div className={`rounded-card border border-rule bg-paper p-5 ${className}`}>{children}</div>
  );
}

/** A titled region. The heading is a real <h2> in the display face. */
export function Section({ title, description, children, className = '' }: {
  title: string; description?: ReactNode; children: ReactNode; className?: string;
}) {
  return (
    <section className={`rounded-card border border-rule bg-paper p-5 ${className}`}>
      <h2 className="font-display text-lg leading-tight text-ink">{title}</h2>
      {description && <p className="mt-1 max-w-[70ch] text-sm text-muted">{description}</p>}
      <div className="mt-4">{children}</div>
    </section>
  );
}

export function Stat({ label, value, sub, emphasis }: {
  label: string; value: ReactNode; sub?: ReactNode; emphasis?: boolean;
}) {
  return (
    <div className={`rounded-card border p-4 ${emphasis
      ? 'border-ink bg-ink text-paper' : 'border-rule bg-paper text-ink'}`}>
      <div className={`text-xs uppercase tracking-wide ${emphasis ? 'text-paper-3' : 'text-muted'}`}>
        {label}
      </div>
      <div className="mt-1 text-2xl font-semibold tabular-nums">{value}</div>
      {sub && <div className={`mt-1 text-xs ${emphasis ? 'text-paper-3' : 'text-muted'}`}>{sub}</div>}
    </div>
  );
}

export function Badge({ tone = 'neutral', children }: { tone?: Tone; children: ReactNode }) {
  return (
    <span className={`inline-flex items-center rounded px-2 py-0.5 text-xs font-semibold tracking-wide ${TONE[tone].solid}`}>
      {children}
    </span>
  );
}

export function Callout({ tone = 'neutral', title, children, className = '' }: {
  tone?: Tone; title?: ReactNode; children?: ReactNode; className?: string;
}) {
  const t = TONE[tone];
  return (
    <div className={`rounded-card border ${t.rule} ${t.bg} p-4 text-sm ${className}`}>
      {title && <div className={`font-semibold ${t.text}`}>{title}</div>}
      {children && <div className={`${title ? 'mt-1 ' : ''}text-ink-2`}>{children}</div>}
    </div>
  );
}

/** Progressive disclosure on a native <details>: keyboard and screen-reader
 *  behaviour come from the platform, not from a script. */
export function Disclosure({ summary, children, defaultOpen = false }: {
  summary: ReactNode; children: ReactNode; defaultOpen?: boolean;
}) {
  return (
    <details open={defaultOpen} className="group rounded-card border border-rule bg-paper">
      <summary className="flex min-h-control cursor-pointer list-none items-center justify-between gap-3 px-4 text-sm font-medium text-ink-2 hover:text-ink">
        <span>{summary}</span>
        <span aria-hidden="true" className="text-muted transition-transform duration-short ease-out group-open:rotate-90">›</span>
      </summary>
      <div className="border-t border-rule px-4 py-4">{children}</div>
    </details>
  );
}

/** A placeholder with the shape of what is coming. The pulse is switched off
 *  by the global reduced-motion rule. */
export function Skeleton({ label, className = 'h-24' }: { label: string; className?: string }) {
  return (
    <div role="status" className={`animate-pulse rounded-card bg-paper-3 ${className}`}>
      <span className="sr-only">Loading {label}…</span>
    </div>
  );
}

/** A failure a visitor can read. The raw status line stays behind a
 *  disclosure for anyone debugging. */
export function ErrorState({ error, onRetry, what }: {
  error: unknown; onRetry?: () => void; what?: string;
}) {
  const technical = technicalDetail(error);
  return (
    <div role="alert" className="rounded-card border border-negative-rule bg-negative-bg p-4 text-sm">
      <div className="font-semibold text-negative">
        {what ? `Could not load ${what}.` : 'Something went wrong.'}
      </div>
      <p className="mt-1 text-ink-2">{publicMessage(error)}</p>
      <div className="mt-2 flex flex-wrap items-center gap-3">
        {onRetry && (
          <button type="button" onClick={onRetry}
            className="min-h-control rounded-input border border-rule bg-paper px-3 text-sm font-medium text-ink hover:border-rule-firm">
            Try again
          </button>
        )}
        {technical && (
          <details className="text-xs text-muted">
            <summary className="cursor-pointer">Technical detail</summary>
            <code className="mt-1 block break-all font-mono">{technical}</code>
          </details>
        )}
      </div>
    </div>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded-card border border-dashed border-rule bg-paper-2 p-6 text-center">
      <div className="font-medium text-ink-2">{title}</div>
      {children && <div className="mt-1 text-sm text-muted">{children}</div>}
    </div>
  );
}
