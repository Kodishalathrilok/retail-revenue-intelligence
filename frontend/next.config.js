/** @type {import('next').NextConfig} */
module.exports = {
  // The causal endpoint runs a difference-in-differences fit over a 36,801-row
  // panel and takes ~12s warm. Next's dev proxy defaults well below that, so
  // the request was being cut off and surfacing as a 500 with no useful cause.
  //
  // 60s covers the slowest measured endpoint with headroom. It is not a fix for
  // slowness -- it is an acknowledgement that this endpoint is genuinely slow
  // and the proxy should not misreport that as a failure.
  experimental: {
    proxyTimeout: 60_000,
  },
  // Response headers for the HTML pages. These are the safe, high-value set
  // that cannot break a Next app: clickjacking (frame-ancestors / X-Frame),
  // MIME sniffing, referrer leakage, HTTPS pinning, and a default-deny on the
  // powerful browser features this app never uses. A full script-src CSP is
  // deliberately omitted: it needs per-request nonces through middleware to
  // survive Next's inline hydration, and a wrong one fails silently on the live
  // site. frame-ancestors/base-uri/object-src below need no nonce and are safe.
  async headers() {
    const csp = "frame-ancestors 'none'; base-uri 'self'; object-src 'none'";
    return [{
      source: '/:path*',
      headers: [
        { key: 'X-Content-Type-Options', value: 'nosniff' },
        { key: 'X-Frame-Options', value: 'DENY' },
        { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
        { key: 'Strict-Transport-Security', value: 'max-age=63072000; includeSubDomains' },
        { key: 'Permissions-Policy', value: 'camera=(), microphone=(), geolocation=()' },
        { key: 'Content-Security-Policy', value: csp },
      ],
    }];
  },
  async rewrites() {
    // In production the API runs as a Vercel Python function on the SAME
    // origin, and vercel.json routes /api/v1/* to it. Rewriting here as well
    // would send requests to a localhost address that does not exist there.
    //
    // So this proxy is development-only: it points the browser at the local
    // uvicorn process during `next dev`.
    if (process.env.VERCEL) return [];
    const api = process.env.NEXT_PUBLIC_API_BASE || 'http://127.0.0.1:8010';
    // /health too: the Ask page reads the data tier from it for its evidence,
    // and vercel.json already routes it in production.
    return [
      { source: '/api/:path*', destination: `${api}/api/:path*` },
      { source: '/health', destination: `${api}/health` },
    ];
  },
};
