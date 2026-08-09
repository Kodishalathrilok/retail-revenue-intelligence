/** Thin API client. Every response carries a `meta` block with the data basis. */

const BASE = process.env.NEXT_PUBLIC_API_BASE ?? '';

export async function get<T>(path: string): Promise<T> {
  const r = await fetch(`${BASE}${path}`, { cache: 'no-store' });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText} on ${path}`);
  return r.json();
}

export async function post<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText} on ${path}`);
  return r.json();
}

export const fmtMoney = (v: number | string | null | undefined) =>
  v === null || v === undefined ? '—'
    : Number(v).toLocaleString(undefined, { style: 'currency', currency: 'USD',
        maximumFractionDigits: 0 });

export const fmtNum = (v: number | string | null | undefined, dp = 0) =>
  v === null || v === undefined ? '—'
    : Number(v).toLocaleString(undefined, { maximumFractionDigits: dp });
