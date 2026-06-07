import type { NextConfig } from "next";

// 같은-오리진 API 프록시 — 브라우저(폰 포함)는 공개 URL 1개로 페이지+API 다 받는다(CORS 불요).
// `/api/*` → 로컬 API(127.0.0.1:8000). API base는 lib/api.ts에서 기본 `/api`.
// 대상은 env로 덮을 수 있게(배포 유연성): API_PROXY_TARGET 미설정 시 로컬 기본.
const API_PROXY_TARGET = process.env.API_PROXY_TARGET ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_PROXY_TARGET}/:path*` }];
  },
};

export default nextConfig;
