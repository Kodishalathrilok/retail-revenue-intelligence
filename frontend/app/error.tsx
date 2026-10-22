'use client';

/**
 * Route-level error boundary. Without one, an unhandled render error in any
 * page shows Next's default overlay in development and a blank screen in
 * production. This keeps the masthead and footer (it renders inside the root
 * layout) and gives the visitor the one action that helps -- try again -- in
 * the app's own vocabulary. It never shows the error's message: that can carry
 * internal detail, and it means nothing to a visitor.
 */

import { useEffect } from 'react';

export default function Error({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    // The digest is Next's server-side correlation id; the message stays out of
    // the UI but is worth having in the browser console while debugging.
    console.error('page error', error.digest ?? '', error);
  }, [error]);

  return (
    <section className="mx-auto max-w-xl rounded-card border border-rule bg-paper p-6 sm:p-8">
      <p className="font-mono text-2xs uppercase tracking-[0.08em] text-muted">Something went wrong</p>
      <h1 className="mt-2 font-display text-2xl leading-tight text-ink">
        This page could not be shown
      </h1>
      <p className="mt-3 max-w-[60ch] text-base text-ink-2">
        An unexpected error interrupted this page. The rest of the site still works, and
        trying again often clears it.
      </p>
      <div className="mt-5 flex flex-wrap gap-3">
        <button
          type="button"
          onClick={reset}
          className="inline-flex min-h-control items-center rounded-input bg-ink px-5 text-sm font-medium text-paper hover:bg-ink-2"
        >
          Try again
        </button>
        <a
          href="/"
          className="inline-flex min-h-control items-center rounded-input border border-rule bg-paper px-5 text-sm font-medium text-ink hover:border-rule-firm"
        >
          Back to the overview
        </a>
      </div>
    </section>
  );
}
