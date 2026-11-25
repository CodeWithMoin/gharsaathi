import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  outputFileTracingRoot: process.cwd(),
  images: {
    // Only Clerk avatars go through the optimizer; provider product images render unoptimized.
    // A "**" pattern would make /_next/image fetch any https URL (open proxy / SSRF).
    remotePatterns: [
      { protocol: "https", hostname: "img.clerk.com" },
      { protocol: "https", hostname: "images.clerk.dev" },
    ],
  },
  async headers() {
    const immutable = [
      { key: "Cache-Control", value: "public, max-age=31536000, immutable" },
      { key: "Cross-Origin-Resource-Policy", value: "same-origin" },
    ];
    return [
      { source: "/models/:path*.onnx", headers: immutable },
      // The service worker must update promptly and may only control this origin's root.
      { source: "/sw.js", headers: [{ key: "Cache-Control", value: "no-cache" }, { key: "Service-Worker-Allowed", value: "/" }] },
      { source: "/ort/:path*", headers: [{ key: "Cache-Control", value: "public, max-age=86400" }, immutable[1]] },
    ];
  },
};

export default nextConfig;
