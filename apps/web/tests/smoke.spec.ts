import { expect, test } from "@playwright/test";

/**
 * 앱 셸 스모크 — Phase 0 화면 검증 게이트 (키리스).
 * 검증: 셸 렌더(필터 패널·지도 컨테이너) + 키 부재 graceful(placeholder) + 콘솔 에러 0 + 스크린샷.
 * 라이브 지도/마커는 사람 시각 확인(JS 키 필요).
 */
test("app shell renders keyless with zero console errors", async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(msg.text());
  });
  page.on("pageerror", (err) => {
    consoleErrors.push(err.message);
  });

  await page.goto("/", { waitUntil: "networkidle" });

  await expect(page.getByTestId("app-title")).toBeVisible();
  await expect(page.getByTestId("filter-panel")).toBeVisible();
  await expect(page.getByTestId("map-container")).toBeVisible();
  // 키 부재 → placeholder graceful (게이트는 키리스 빌드)
  await expect(page.getByTestId("map-placeholder")).toBeVisible();
  // R1: gym은 hard filter 아님 — soft 랭킹 선호로만 등장(P1-4). hard 숫자필드엔 gym 없음.
  await expect(page.getByTestId("soft-prefs")).toBeVisible();
  await expect(page.getByTestId("gym-pref")).toBeVisible();

  await page.screenshot({ path: "test-results/home.png", fullPage: true });

  expect(consoleErrors, `console errors:\n${consoleErrors.join("\n")}`).toEqual([]);
});
