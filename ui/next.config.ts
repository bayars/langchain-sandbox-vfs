import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Allow the dev server to serve JS/HMR to remote hosts on the LAN
  allowedDevOrigins: ["10.0.0.172"],
};

export default nextConfig;
