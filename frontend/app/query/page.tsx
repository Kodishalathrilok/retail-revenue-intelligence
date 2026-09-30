'use client';

/**
 * Ask -- the product's primary interaction.
 *
 * question → progress → answer → chart (only when the result's shape supports
 * one) → rows → "Why should I trust this?" → limitations.
 *
 * This page used to lead with the validation trail and put the result last.
 * The trail is still here, one click deep inside the evidence: it is what
 * makes the answer trustworthy, but it is not the answer. Every figure on the
 * page is formatted from the rows PostgreSQL returned (lib/answer.mjs); the
 * language model only drafted the SQL.
 */

import Link from 'next/link';
import { useEffect, useId, useState } from 'react';
import {
  AnswerCard, AskProgress, EvidencePanel, ResultChart, ResultTable, TABLE_ROWS_SHOWN,
  ValidationTrail, type Health, type QueryResult,
} from '@/components/ask';
import { Callout, Disclosure, ErrorState, type Tone } from '@/components/ui';
import { answerSentence, chartSpec, evidence, outcome } from '@/lib/answer.mjs';
import { ApiError, get, post } from '@/lib/api';
import { EXAMPLES } from '@/lib/examples';

// NLQueryRequest: question must be 3..500 characters.
const MIN_LEN = 3;
const MAX_LEN = 500;

const TONE: Record<string, Tone> = {
  refused: 'caution', unsupported: 'neutral', ambiguous: 'neutral', too_broad: 'caution',
  forecast: 'accent', unanswerable: 'neutral', failed: 'negative',
};

// Refusals from the abuse protection carry visitor-ready text; these are only
// the headings above it.
const REFUSAL_TITLE: Record<string, string> = {
  RATE_LIMITED: 'Too many questions in a short time',
  DAILY_LLM_CAP: "Today's AI limit has been reached",
  AI_PROTECTION_UNAVAILABLE: 'AI answers are temporarily unavailable',
  AI_UNAVAILABLE: 'AI answers are temporarily unavailable',
  ORIGIN_NOT_ALLOWED: 'Not available from here',
};

function Outcome({ res, asked, health }: { res: QueryResult; asked: string; health: Health }) {
  const o = outcome(res);

  if (o.kind === 'answer') {
    const spec = chartSpec(res);
    return (
      <div className="space-y-4">
        <p className="text-xs text-muted">
          You asked: <span className="text-ink-2">{asked}</span>
        </p>
        <AnswerCard sentence={answerSentence(res)} result={res} />
        {spec && <ResultChart spec={spec} result={res} />}
        {res.rows.length > 0 && <ResultTable result={res} />}
        <EvidencePanel e={evidence(res, health, TABLE_ROWS_SHOWN)} attempts={res.attempts} />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <p className="text-xs text-muted">
        You asked: <span className="text-ink-2">{asked}</span>
      </p>
      <Callout tone={TONE[o.kind] ?? 'neutral'} title={o.title}>
        {o.message && <p>{o.message}</p>}
        {o.clarification && (
          <p className="mt-2">
            <span className="font-medium text-ink">What you can ask instead: </span>
            {o.clarification}
          </p>
        )}
        {o.kind === 'forecast' && (
          <p className="mt-2">
            <Link href="/forecast" className="font-medium text-accent underline underline-offset-2">
              Open the forecast
            </Link>
          </p>
        )}
      </Callout>
      {o.kind === 'failed' && res.attempts.length > 0 && (
        <Disclosure summary="What was tried (technical)">
          <ValidationTrail attempts={res.attempts} />
        </Disclosure>
      )}
    </div>
  );
}

export default function AskPage() {
  const id = useId();
  const [q, setQ] = useState('');
  const [asked, setAsked] = useState('');
  const [res, setRes] = useState<QueryResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [health, setHealth] = useState<Health>(null);

  useEffect(() => {
    // ?q= pre-fills the question (links from other pages); it never auto-runs,
    // so arriving at a link does not spend a model call.
    const pre = new URLSearchParams(window.location.search).get('q');
    if (pre) setQ(pre.slice(0, MAX_LEN));
    // The data-tier line in the evidence. If /health cannot be read, the
    // evidence says the tier was not reported rather than asserting one.
    get<Health>('/health').then(setHealth).catch(() => setHealth(null));
  }, []);

  async function ask(question: string) {
    const text = question.trim();
    if (text.length < MIN_LEN || busy) return;
    setQ(text); setAsked(text); setBusy(true); setErr(null); setRes(null);
    try {
      setRes(await post<QueryResult>('/api/v1/ai/query', { question: text, max_attempts: 2 }));
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="max-w-4xl space-y-8">
      <header>
        <p className="font-mono text-2xs uppercase tracking-[0.08em] text-muted">
          Ask · published aggregate data
        </p>
        <h1 className="mt-2 font-display text-3xl leading-tight text-ink sm:text-4xl">Ask the data</h1>
        <p className="mt-3 max-w-[65ch] text-base text-ink-2">
          Ask a business question in plain English. A language model drafts the SQL and never
          computes a number: PostgreSQL does, after the query passes read-only validation checks.
          The evidence behind every answer is one click away.
        </p>
      </header>

      <form
        onSubmit={(e) => { e.preventDefault(); ask(q); }}
        className="rounded-card border border-rule bg-paper p-5"
      >
        <label htmlFor={id} className="block text-sm font-medium text-ink">Your question</label>
        <textarea
          id={id}
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask(q); }
          }}
          maxLength={MAX_LEN}
          rows={2}
          aria-describedby={`${id}-hint`}
          placeholder="e.g. Which 5 departments have the highest total revenue?"
          className="mt-2 w-full resize-y rounded-input border border-rule bg-paper p-3 text-base text-ink"
        />
        <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
          <p id={`${id}-hint`} className="text-xs text-muted">
            Enter to ask · Shift+Enter for a new line · {q.length}/{MAX_LEN}
          </p>
          <button
            type="submit"
            disabled={busy || q.trim().length < MIN_LEN}
            className="min-h-control rounded-input bg-ink px-6 text-sm font-medium text-paper hover:bg-ink-2 disabled:opacity-40"
          >
            {busy ? 'Working…' : 'Ask'}
          </button>
        </div>
      </form>

      <section aria-labelledby={`${id}-examples`}>
        <h2 id={`${id}-examples`} className="text-sm font-medium text-ink-2">Verified questions</h2>
        <p className="mt-1 text-xs text-muted">
          Each one is checked against a reference answer on the published data.
        </p>
        <ul className="mt-3 flex flex-wrap gap-2">
          {EXAMPLES.map((ex) => (
            <li key={ex.question}>
              <button
                type="button"
                disabled={busy}
                onClick={() => ask(ex.question)}
                className="min-h-control rounded-input border border-rule bg-paper px-3 py-2 text-left text-sm text-ink-2 hover:border-rule-firm hover:text-ink disabled:opacity-40"
              >
                {ex.refusal && <span className="mr-1.5 font-semibold text-caution">Safety check:</span>}
                {ex.question}
              </button>
            </li>
          ))}
        </ul>
      </section>

      <div className="space-y-4">
        {busy && <AskProgress />}

        {!busy && err instanceof ApiError && err.code ? (
          <Callout tone="caution" title={REFUSAL_TITLE[err.code] ?? 'Not answered'}>
            {err.message}
          </Callout>
        ) : !busy && err != null ? (
          <ErrorState error={err} what="an answer" onRetry={() => ask(asked)} />
        ) : null}

        {!busy && res && <Outcome res={res} asked={asked} health={health} />}
      </div>
    </div>
  );
}
