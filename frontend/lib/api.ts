/** Thin API client. Every response carries a `meta` block with the data basis. */

const BASE = process.env.NEXT_PUBLIC_API_BASE ?? '';

/** Error with the API's machine-readable refusal code, when it sent one. */
export class ApiError extends Error {
  constructor(message: string, readonly status: number, readonly code?: string) {
    super(message);
  }
}

/**
 * Refusals from the AI endpoints' abuse protection (rate limit, daily cap,
 * origin) arrive as {error, message}, where `message` is written to be shown
 * to a visitor as is. Anything else falls back to the status line.
 */
async function fail(r: Response, path: string): Promise<never> {
  let body: { error?: string; message?: string } | null = null;
  try { body = await r.json(); } catch { /* not JSON */ }
  if (body?.error && body.message) throw new ApiError(body.message, r.status, body.error);
  throw new ApiError(`${r.status} ${r.statusText} on ${path}`, r.status);
}

export async function get<T>(path: string): Promise<T> {
  const r = await fetch(`${BASE}${path}`, { cache: 'no-store' });
  if (!r.ok) return fail(r, path);
  return r.json();
}

export async function post<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!r.ok) return fail(r, path);
  return r.json();
}

export const fmtMoney = (v: number | string | null | undefined) =>
  v === null || v === undefined ? '—'
    : Number(v).toLocaleString(undefined, { style: 'currency', currency: 'USD',
        maximumFractionDigits: 0 });

export const fmtNum = (v: number | string | null | undefined, dp = 0) =>
  v === null || v === undefined ? '—'
    : Number(v).toLocaleString(undefined, { maximumFractionDigits: dp });
