'use client';

/**
 * NL query view.
 *
 * The point of this screen is the validation trail, not the answer. Every gate
 * the generated SQL passed or failed is shown, along with each retry and the
 * error text fed back to the model. A rejected query is a successful outcome
 * for this interface, so rejections are rendered as prominently as results.
 */

import { useState } from 'react';
import { CaveatBar } from '@/components/Caveats';
import { post } from '@/lib/api';

type Stage = { stage: string; passed: boolean; detail: string; duration_ms: number | null };
type Attempt = {
  attempt: number; sql: string; stages: Stage[];
  rejected_reason: string | null; error_fed_back: string | null;
};
type Result = {
  question: string; succeeded: boolean; sql: string | null;
  columns: string[]; rows: Record<string, unknown>[]; row_count: number;
  attempts: Attempt[]; total_duration_ms: number; provider: string | null;
  failure_reason: string | null;
};

const EXAMPLES = [
  'Which 5 departments have the highest total revenue?',
  'What is the average basket value by month?',
  'Which 10 commodities have the highest reorder rate?',
  'Delete every transaction from the database',
];

const GATE_HELP: Record<string, string> = {
  shape: 'Exactly one statement, and it must be a SELECT',
  keywords: 'No DDL, DML or dangerous functions (checked with strings and comments stripped)',
  explain: 'EXPLAIN without ANALYZE; rejected above a cost ceiling',
  execute: 'Runs under a hard statement timeout and a row cap',
  result_shape: 'Result must have columns and must not be truncated',
};

export default function QueryPage() {
  const [q, setQ] = useState(EXAMPLES[0]);
  const [res, setRes] = useState<Result | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function run() {
    setBusy(true); setErr(null); setRes(null);
    try {
      setRes(await post<Result>('/api/v1/ai/query', { question: q, max_attempts: 2 }));
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Natural language query</h1>
        <p className="mt-1 text-sm text-slate-500">
          The model proposes SQL. It never executes anything and never computes a
          number — every value below was produced by Postgres.
        </p>
      </div>

      <div className="rounded-lg border border-slate-200 bg-white p-5">
        <textarea
          value={q} onChange={(e) => setQ(e.target.value)} rows={2}
          className="w-full resize-none rounded-md border border-slate-300 p-3 text-sm"
        />
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <button onClick={run} disabled={busy}
            className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-40">
            {busy ? 'Running…' : 'Run'}
          </button>
          {EXAMPLES.map((e) => (
            <button key={e} onClick={() => setQ(e)}
              className="rounded-md border border-slate-200 px-2.5 py-1 text-xs text-slate-600 hover:bg-slate-50">
              {e.length > 42 ? `${e.slice(0, 42)}…` : e}
            </button>
          ))}
        </div>
        <p className="mt-2 text-xs text-slate-400">
          The last example is adversarial — it should be rejected, or answered
          with a harmless SELECT.
        </p>
      </div>

      {err && (
        <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          {err} — is the API running with a provider key configured?
        </div>
      )}

      {res && (
        <>
          <div className={`rounded-lg border p-4 ${res.succeeded
            ? 'border-emerald-200 bg-emerald-50' : 'border-amber-300 bg-amber-50'}`}>
            <div className="flex items-center gap-3">
              <span className={`rounded px-2 py-0.5 text-xs font-semibold ${res.succeeded
                ? 'bg-emerald-600 text-white' : 'bg-amber-600 text-white'}`}>
                {res.succeeded ? 'ACCEPTED' : 'REJECTED'}
              </span>
              <span className="text-sm text-slate-600">
                {res.attempts.length} attempt{res.attempts.length === 1 ? '' : 's'} ·{' '}
                {res.total_duration_ms.toFixed(0)} ms · provider {res.provider}
              </span>
            </div>
            {res.failure_reason && (
              <p className="mt-2 text-sm text-amber-800">{res.failure_reason}</p>
            )}
          </div>

          {res.attempts.map((a) => (
            <section key={a.attempt} className="rounded-lg border border-slate-200 bg-white p-5">
              <h2 className="text-sm font-semibold">
                Attempt {a.attempt}
                {a.rejected_reason && (
                  <span className="ml-2 rounded bg-amber-100 px-2 py-0.5 text-xs font-normal text-amber-800">
                    rejected
                  </span>
                )}
              </h2>

              <pre className="mt-3 overflow-x-auto rounded bg-slate-900 p-3 text-xs leading-relaxed text-slate-100">
                {a.sql}
              </pre>

              <ol className="mt-4 space-y-1.5">
                {a.stages.map((s) => (
                  <li key={s.stage} className="flex items-start gap-3 text-sm">
                    <span className={`mt-0.5 w-16 shrink-0 rounded px-1.5 py-0.5 text-center text-[10px] font-semibold ${
                      s.passed ? 'bg-emerald-100 text-emerald-700' : 'bg-red-100 text-red-700'}`}>
                      {s.passed ? 'PASS' : 'REJECT'}
                    </span>
                    <span className="w-28 shrink-0 font-mono text-xs text-slate-700">{s.stage}</span>
                    <span className="text-xs text-slate-600">
                      {s.detail}
                      <span className="block text-slate-400">{GATE_HELP[s.stage]}</span>
                    </span>
                  </li>
                ))}
              </ol>

              {a.error_fed_back && (
                <div className="mt-3 rounded border border-slate-200 bg-slate-50 p-3">
                  <div className="text-xs font-semibold text-slate-600">Fed back to the model</div>
                  <div className="mt-1 font-mono text-xs text-slate-700">{a.error_fed_back}</div>
                </div>
              )}
            </section>
          ))}

          {res.succeeded && res.rows.length > 0 && (
            <section className="rounded-lg border border-slate-200 bg-white p-5">
              <h2 className="text-sm font-semibold">
                Result — {res.row_count} row{res.row_count === 1 ? '' : 's'}
              </h2>
              <div className="mt-3 overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-slate-200 text-left text-xs uppercase text-slate-500">
                      {res.columns.map((c) => <th key={c} className="py-2 pr-4">{c}</th>)}
                    </tr>
                  </thead>
                  <tbody>
                    {res.rows.slice(0, 50).map((r, i) => (
                      <tr key={i} className="border-b border-slate-100">
                        {res.columns.map((c) => (
                          <td key={c} className="py-1.5 pr-4 tabular-nums">{String(r[c])}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}
        </>
      )}

      <CaveatBar calendar revenue />
    </div>
  );
}
