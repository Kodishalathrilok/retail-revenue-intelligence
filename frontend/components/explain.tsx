'use client';

/**
 * "Explain": what a figure means, where it came from, how it was calculated,
 * and what it cannot establish.
 *
 * Opt-in (a closed <details>) and static: the four rows come from
 * lib/metrics.mjs, which restates the SQL and the estimators. No language
 * model writes any of it. `notes` adds what the API itself reported alongside
 * the figure (a basis line, a caveat, a test verdict), shown verbatim.
 */

import { useState } from 'react';
import { Disclosure, FactRow } from '@/components/ui';
import { metric } from '@/lib/metrics.mjs';

export function Explain({ metrics, notes = {} }: {
  metrics: string[];
  notes?: Record<string, (string | null | undefined)[]>;
}) {
  const [key, setKey] = useState(metrics[0]);
  const m = metric(key);
  const reported = (notes[key] ?? []).filter((n): n is string => Boolean(n));

  return (
    <Disclosure summary={metrics.length > 1 ? 'Explain these figures' : 'Explain this figure'}>
      {metrics.length > 1 && (
        <div role="group" aria-label="Choose a figure to explain" className="mb-3 flex flex-wrap gap-2">
          {metrics.map((k) => (
            <button key={k} type="button" aria-pressed={k === key} onClick={() => setKey(k)}
              className={`min-h-control rounded-input border px-3 text-sm ${k === key
                ? 'border-ink bg-ink font-medium text-paper'
                : 'border-rule bg-paper text-ink-2 hover:border-rule-firm hover:text-ink'}`}>
              {metric(k).label}
            </button>
          ))}
        </div>
      )}
      <h3 className="font-medium text-ink">{m.label}</h3>
      <dl>
        <FactRow term="Definition">{m.definition}</FactRow>
        <FactRow term="Source">
          {m.source}
          <span className="mt-1 block text-xs text-muted">
            Endpoint <code className="font-mono">{m.endpoint}</code> · reads{' '}
            {m.tables.map((t: string, i: number) => (
              <span key={t}>{i > 0 && ', '}<code className="font-mono">{t}</code></span>
            ))}
          </span>
        </FactRow>
        <FactRow term="Calculation">{m.calculation}</FactRow>
        <FactRow term="Limitation">{m.limitation}</FactRow>
        {reported.length > 0 && (
          <FactRow term="Reported with it">
            <ul className="list-disc space-y-1 pl-4">
              {reported.map((n) => <li key={n}>{n}</li>)}
            </ul>
          </FactRow>
        )}
      </dl>
    </Disclosure>
  );
}
