// The browser talks only to this server; /api/v1/* is forwarded to the read-only API
// (`vcp api serve`, 127.0.0.1:8000), so no cross-origin requests are needed.
const API = process.env.VCP_API_URL ?? "http://127.0.0.1:8000";

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  async rewrites() {
    return [{ source: "/api/v1/:path*", destination: `${API}/api/v1/:path*` }];
  },
};

export default nextConfig;
