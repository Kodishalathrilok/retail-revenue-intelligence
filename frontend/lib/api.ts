/** Thin API client. Every response carries a `meta` block with the data basis. */

const BASE = process.env.NEXT_PUBLIC_API_BASE ?? '';

/**
 * Error from the API. `code` is the machine-readable refusal code when the
 * API sent one; `technical` is the raw status line, for a disclosure -- never
 * for the headline a visitor reads.
 */
export class ApiError extends Error {
  constructor(message: string, readonly status: number, readonly code?: string,
              readonly technical?: string) {
    super(message);
  }
}

type Refusal = { error?: string; message?: string };

/**
 * Refusals arrive as {error, message} -- at the top level from the abuse
 * protection handler, or under `detail` from routes that raise HTTPException
 * with a dict (forecast availability, LOCAL_ONLY). Either way `message` is
 * written for a visitor. Anything else keeps only the status line, as
 * `technical`.
 */
async function fail(r: Response, path: string): Promise<never> {
  let body: (Refusal & { detail?: Refusal | string }) | null = null;
  try { body = await r.json(); } catch { /* not JSON */ }
  const technical = `${r.status} ${r.statusText} on ${path}`;
  const refusal = body?.error ? body : typeof body?.detail === 'object' ? body.detail : null;
  if (refusal?.error && refusal.message) {
    throw new ApiError(refusal.message, r.status, refusal.error, technical);
  }
  throw new ApiError(technical, r.status, undefined, technical);
}

/**
 * What a visitor is told when a request fails. Refusals carry their own
 * visitor-ready text; everything else gets a plain sentence. Never a developer
 * hint -- "is the API running? rrip serve" used to be shown on the public site.
 */
export function publicMessage(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.code) return e.message;
    if (e.status === 404) return 'That is not available in this dataset.';
    if (e.status === 422) return 'That request is outside what this analysis supports.';
    if (e.status >= 500) {
      return 'This part of the analysis is temporarily unavailable. Please try again shortly.';
    }
    return 'The request could not be completed.';
  }
  return 'Could not reach the analysis service. Check your connection and try again.';
}

/** The raw detail behind a failure, for a "technical detail" disclosure. */
export function technicalDetail(e: unknown): string | undefined {
  if (e instanceof ApiError) return e.technical;
  if (e instanceof Error) return e.message;
  return undefined;
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
