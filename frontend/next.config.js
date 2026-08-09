/** @type {import('next').NextConfig} */
module.exports = {
  async rewrites() {
    // Proxy API calls to FastAPI in development so the browser sees one origin.
    return [{ source: '/api/:path*', destination: 'http://127.0.0.1:8000/api/:path*' }];
  },
};
