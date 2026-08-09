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
  async rewrites() {
    const api = process.env.NEXT_PUBLIC_API_BASE || 'http://127.0.0.1:8000';
    return [{ source: '/api/:path*', destination: `${api}/api/:path*` }];
  },
};
