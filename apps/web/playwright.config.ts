import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright 스모크 설정 — 화면 검증 게이트(gate-e2e).
 *
 * production 빌드(`next start`)에 붙는다. dev 서버의 HMR/telemetry 로그가
 * 콘솔을 오염시켜 "콘솔 에러 0" 검증을 flaky하게 만드는 것을 피하기 위함.
 * Makefile의 `e2e-web: build-web`이 빌드 선행을 보장하므로 여기선 `next start`만 띄운다.
 */
const PORT = 3100;

export default defineConfig({
  testDir: "./tests",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    screenshot: "only-on-failure",
    trace: "on-first-retry",
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
  ],
  webServer: {
    command: `npm run start -- --port ${PORT}`,
    url: `http://127.0.0.1:${PORT}`,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
