import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  outputFileTracingRoot: process.cwd(),
  images: {
    remotePatterns: [{ protocol: "https", hostname: "**" }],
  },
  async headers() {
    const immutable = [
      { key: "Cache-Control", value: "public, max-age=31536000, immutable" },
      { key: "Cross-Origin-Resource-Policy", value: "same-origin" },
    ];
    return [
      { source: "/models/:path*.onnx", headers: immutable },
      { source: "/ort/:path*", headers: [{ key: "Cache-Control", value: "public, max-age=86400" }, immutable[1]] },
    ];
  },
};

export default nextConfig;
