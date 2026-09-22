import type { Config } from 'tailwindcss';

/* Hallmark · genre: editorial · theme: Almanac (cool-anchored)
 *
 * Additive only. Every value below points at a custom property declared in
 * app/tokens.css, so the token block stays the single source of truth and a
 * class like `text-muted` cannot drift from `var(--color-muted)`.
 *
 * The existing Tailwind slate palette is left in place: the four page files
 * are painted in it, and remapping it here would repaint them silently.
 */
export default {
  content: ['./app/**/*.{ts,tsx}', './components/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        paper: 'var(--color-paper)',
        'paper-2': 'var(--color-paper-2)',
        'paper-3': 'var(--color-paper-3)',
        rule: 'var(--color-rule)',
        'rule-firm': 'var(--color-rule-firm)',
        muted: 'var(--color-muted)',
        'ink-2': 'var(--color-ink-2)',
        ink: 'var(--color-ink)',
        accent: 'var(--color-accent)',
        'accent-ink': 'var(--color-accent-ink)',
        focus: 'var(--color-focus)',
      },
      fontFamily: {
        display: ['var(--font-display)', 'var(--font-display-fallback)'],
        body: ['var(--font-body)', 'var(--font-body-fallback)'],
        mono: ['var(--font-mono)', 'var(--font-mono-fallback)'],
      },
      fontSize: {
        '2xs': 'var(--text-2xs)',
        mast: ['var(--text-mast)', { lineHeight: 'var(--lh-tight)' }],
      },
      minHeight: {
        control: 'var(--control-h)',
      },
      transitionTimingFunction: {
        out: 'var(--ease-out)',
      },
      transitionDuration: {
        short: 'var(--dur-short)',
      },
    },
  },
  plugins: [],
} satisfies Config;
