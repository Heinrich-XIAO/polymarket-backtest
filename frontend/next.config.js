/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "export",
  basePath: "/polymarket-backtest",
  trailingSlash: true,
  images: { unoptimized: true },
  env: {
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL || "https://polymarket-backtest-ir3p.onrender.com",
  },
};

module.exports = nextConfig;
