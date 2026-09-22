/* Hallmark · genre: editorial (switched from modern-minimal — the masthead is an
 *   editorial vocabulary, and N6 is banned under modern-minimal)
 * theme: Almanac (cool-anchored) · paper oklch(97.6% 0.006 250) ·
 *   accent oklch(42% 0.105 210) · display Newsreader + body Libre Franklin
 * nav: N6 Newspaper masthead — knobs: issue-line=below-wordmark, wordmark=xl
 *   (restrained), rule=double
 * footer: Ft4 Dense colophon
 * scope: app shell — no macrostructure (this file is not a page)
 * contrast: pass (40–41) · mobile: pass (34, 49, 51)
 * pre-emit critique: P5 H4 E5 S5 R4 V5
 *
 * Why a masthead on an analytics tool. The previous header asked one flex row to
 * carry identity, navigation and provenance at once; below 474px it could not,
 * and it overflowed the viewport by up to 130px. The masthead separates those
 * three jobs onto three stacked bands, which is both the honest information
 * hierarchy and the reason the mobile overflow disappears rather than being
 * patched.
 *
 * The archetype's own warning is that masthead vocabulary reads as costume on a
 * B2B product. The defence here is that this app genuinely is an edition: a
 * closed 711-day panel, a locked test window, figures that will not move again.
 * The issue line states that edition. It is kept in ledger mono rather than the
 * broadsheet's serif small caps, and the wordmark is capped well below display
 * scale, so the band reads as a data masthead and not as a newspaper set.
 */

import './globals.css';
import { Fragment } from 'react';
import Link from 'next/link';
import type { Metadata } from 'next';
import { Newsreader, Libre_Franklin, JetBrains_Mono } from 'next/font/google';
import { MastheadNav } from '@/components/MastheadNav';

/* `style: ['normal']` is deliberate on the display face: the italic is never
   downloaded, so an italic heading cannot be introduced later by accident.
   `adjustFontFallback: false` because Next ships no metric-override table for
   Newsreader and warns on every compile otherwise; the explicit Georgia stack
   in tokens.css is the fallback instead. */
const display = Newsreader({
  subsets: ['latin'],
  style: ['normal'],
  display: 'swap',
  adjustFontFallback: false,
  variable: '--font-display',
});

const body = Libre_Franklin({
  subsets: ['latin'],
  display: 'swap',
  variable: '--font-body',
});

const mono = JetBrains_Mono({
  subsets: ['latin'],
  display: 'swap',
  variable: '--font-mono',
});

export const metadata: Metadata = {
  title: 'Retail Revenue Intelligence',
  description: 'Analytics over the dunnhumby Complete Journey household panel',
};

/* The edition line, as data rather than a sentence so it wraps by group instead
   of mid-fact. Every figure here is already stated elsewhere in the codebase:
   the panel name and transaction count come from the header this replaces, the
   711-day window and week range from components/Caveats.tsx, the household
   count from src/rrip/ai/causal.py. Nothing here is inferred. */
const EDITION: string[][] = [
  ['dunnhumby Complete Journey', '711 days', 'weeks 1–102'],
  ['2,595,732 transactions', '2,500 households'],
];

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html
      lang="en"
      className={`${display.variable} ${body.variable} ${mono.variable}`}
    >
      <body>
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded focus:bg-ink focus:px-4 focus:py-2 focus:text-sm focus:text-paper"
        >
          Skip to content
        </a>

        <header className="border-b border-rule bg-paper">
          <div className="mx-auto max-w-6xl px-6 pt-5 sm:pt-7">
            {/* Band 1 — identity. Deliberately not an <h1>: every page owns its
                own, and a second one here would flatten the outline. */}
            <div className="text-center">
              <Link
                href="/"
                className="inline-block font-display text-mast tracking-[-0.01em] text-ink"
              >
                Retail Revenue Intelligence
              </Link>
            </div>

            {/* The double rule. Two hairlines with air between them, not one
                heavy rule — the weight comes from the pair, which is what keeps
                it a masthead rule rather than a border. */}
            <div
              aria-hidden="true"
              className="mt-4 h-[3px] border-y border-rule-firm"
            />

            {/* Band 2 — the edition. Mono because it is mostly figures, and
                tabular so the counts sit on a common grid.

                Each fact is a flex item rather than inline text: that puts the
                break opportunity *between* facts, so a narrow viewport wraps
                "711 days" onto its own line instead of splitting it. Inline
                spans with the separator inside the nowrap region gave the line
                no break opportunity at all and it overflowed 63px at 320px. */}
            <div className="py-2.5 font-mono text-2xs tracking-[0.04em] tabular-nums text-muted sm:py-3">
              {EDITION.map((line) => (
                <p
                  key={line[0]}
                  className="flex flex-wrap items-baseline justify-center gap-x-2 leading-relaxed"
                >
                  {line.map((fact, i) => (
                    <Fragment key={fact}>
                      {/* The separator inherits the line's own colour rather
                          than a lighter rule tone — at 11px the middot recedes
                          on size alone, and tinting it down put it at 2.25:1. */}
                      {i > 0 && <span aria-hidden="true">·</span>}
                      <span className="whitespace-nowrap">{fact}</span>
                    </Fragment>
                  ))}
                </p>
              ))}
            </div>

            {/* Band 3 — navigation. */}
            <div className="border-t border-rule pt-1">
              <MastheadNav />
            </div>
          </div>
        </header>

        <main id="main" className="mx-auto max-w-6xl px-6 py-8">
          {children}
        </main>

        {/* Ft4 · Dense colophon. The page previously ended wherever its last
            section happened to stop. A colophon closes the document and is the
            right home for the claim the whole app is built to support — stated
            once here rather than repeated per page. */}
        <footer className="mt-4 border-t border-rule">
          <div className="mx-auto max-w-6xl px-6 py-6">
            <p className="max-w-[68ch] font-mono text-2xs leading-relaxed text-muted">
              rrip 0.1.0 · Every figure on this site is computed by Postgres and
              statsmodels; no language model produces a number. Weeks 1 and 102
              are partial — 5 and 6 days — and are not comparable to a full week.
              Weekday labels are a modelling convention; elapsed intervals,
              month boundaries and year-over-year comparisons are real.
            </p>
          </div>
        </footer>
      </body>
    </html>
  );
}
