'use client';

/**
 * Masthead navigation (Hallmark N6 · nav row).
 *
 * Split out of app/layout.tsx purely so the layout can stay a server component
 * and keep exporting `metadata` — this is the only part of the shell that needs
 * the pathname.
 *
 * The shell previously gave no indication of which of the four views you were
 * looking at. It does now, and the indicator is carried on two channels: an
 * accent rule beneath the label and `aria-current="page"`. Colour alone would
 * be the only signal for a sighted mouse user and no signal at all for anyone
 * else.
 */

import Link from 'next/link';
import { usePathname } from 'next/navigation';

const NAV = [
  { href: '/', label: 'Overview' },
  { href: '/query', label: 'NL Query' },
  { href: '/forecast', label: 'Forecast' },
  { href: '/causal', label: 'Causal' },
];

export function MastheadNav() {
  const pathname = usePathname();

  return (
    <nav aria-label="Primary">
      {/* flex-wrap, not a fixed row: four labels centred on a 320px viewport
          wrap to two lines cleanly instead of forcing the page sideways. */}
      <ul className="-mx-2 flex flex-wrap items-center justify-center">
        {NAV.map((n) => {
          const active =
            n.href === '/' ? pathname === '/' : pathname.startsWith(n.href);

          return (
            <li key={n.href}>
              <Link
                href={n.href}
                aria-current={active ? 'page' : undefined}
                /* whitespace-nowrap: "NL Query" wrapped to two lines at every
                   width below 1024px in the previous header, which reads as a
                   styling bug rather than a choice.

                   The bottom border is always 2px and only changes colour, so
                   activating a link cannot shift the row's height. */
                className={[
                  'flex min-h-control items-center whitespace-nowrap px-3',
                  'border-b-2 text-sm transition-[color,border-color]',
                  'duration-short ease-out',
                  active
                    ? 'border-accent font-medium text-ink'
                    : 'border-transparent text-ink-2 hover:border-rule hover:text-ink',
                ].join(' ')}
              >
                {n.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
