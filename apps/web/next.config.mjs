/** @type {import('next').NextConfig} */
const apiUrl = process.env.LK_API_INTERNAL_URL || "http://127.0.0.1:8000";

const nextConfig = {
  // Standalone output is used by the Docker image. It needs symlink permission, which Windows hosts
  // without Developer Mode lack; set LK_WEB_STANDALONE=0 to build locally there.
  output: process.env.LK_WEB_STANDALONE === "0" ? undefined : "standalone",
  poweredByHeader: false,
  // The browser only ever talks to this origin; /api is proxied to the internal API service,
  // which keeps the session cookie same-origin and the API off the network.
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${apiUrl}/api/:path*` }];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "no-referrer" },
        ],
      },
    ];
  },
};

export default nextConfig;
