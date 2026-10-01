/**
 * The decision workflow: Detect → Explain → Predict → Investigate.
 *
 * One strip, the same on every page, so the three engines read as steps of one
 * product instead of three unrelated tools. The steps are lib/overview.mjs
 * STEPS; each one is a real page or section, and the links claim nothing about
 * what the next page will find.
 */

import Link from 'next/link';
import { STEPS } from '@/lib/overview.mjs';

export function Workflow({ current, heading }: { current?: string; heading?: string }) {
  return (
    <nav aria-label="Decision workflow">
      {heading && <h2 className="mb-3 font-display text-lg leading-tight text-ink">{heading}</h2>}
      <ol className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {STEPS.map((s, i) => {
          const here = s.key === current;
          return (
            <li key={s.key}>
              <Link href={s.href} aria-current={here ? 'step' : undefined}
                className={`flex h-full flex-col rounded-card border bg-paper p-4 ${here
                  ? 'border-ink' : 'border-rule hover:border-rule-firm'}`}>
                <span className="font-mono text-2xs uppercase tracking-[0.08em] text-muted">
                  {i + 1} · {s.verb}{here ? ' · you are here' : ''}
                </span>
                <span className="mt-1 font-display text-lg leading-snug text-ink">{s.question}</span>
                {/* The blurb is dropped on a phone: four full cards would push
                    the page's own content a screen further down. */}
                <span className="mt-1 hidden text-sm text-ink-2 sm:block">{s.blurb}</span>
              </Link>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
