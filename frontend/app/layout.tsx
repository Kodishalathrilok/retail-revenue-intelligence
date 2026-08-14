import './globals.css';
import Link from 'next/link';
import type { Metadata } from 'next';

export const metadata: Metadata = {
  title: 'Retail Revenue Intelligence',
  description: 'Analytics over the dunnhumby Complete Journey household panel',
};

const NAV = [
  { href: '/', label: 'Overview' },
  { href: '/query', label: 'NL Query' },
  { href: '/forecast', label: 'Forecast' },
  { href: '/causal', label: 'Causal' },
];

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header className="border-b border-slate-200 bg-white">
          <div className="mx-auto flex max-w-6xl items-center gap-6 px-6 py-4">
            <span className="font-semibold tracking-tight">
              Retail Revenue Intelligence
            </span>
            <nav className="flex gap-4 text-sm text-slate-600">
              {NAV.map((n) => (
                <Link key={n.href} href={n.href} className="hover:text-slate-900">
                  {n.label}
                </Link>
              ))}
            </nav>
            <span className="ml-auto text-xs text-slate-400">
              dunnhumby Complete Journey · 2,595,732 transactions
            </span>
          </div>
        </header>
        <main className="mx-auto max-w-6xl px-6 py-8">{children}</main>
      </body>
    </html>
  );
}
