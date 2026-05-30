import { expect, test } from "@playwright/test";

/**
 * 홈 스모크 — Phase 0 화면 검증 게이트.
 * 검증: (1) 홈 렌더, (2) 핵심 엘리먼트 존재, (3) 콘솔 에러 0, (4) 스크린샷 산출.
 * 기능 로직 없음. T0-1+에서 실제 화면이 붙으면 케이스가 늘어난다.
 */
test("home renders with the key element and zero console errors", async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(msg.text());
  });
  page.on("pageerror", (err) => {
    consoleErrors.push(err.message);
  });

  await page.goto("/", { waitUntil: "networkidle" });

  // (2) 핵심 엘리먼트: 안정 셀렉터(data-testid)로 잡는다.
  const heading = page.getByTestId("home-heading");
  await expect(heading).toBeVisible();

  // (4) 스크린샷 — 리턴팩 첨부용. test-results/(gitignore)에 저장.
  await page.screenshot({ path: "test-results/home.png", fullPage: true });

  // (3) 콘솔 에러 0 — 마지막에 단언해 위 단계의 로그까지 모두 수집.
  expect(consoleErrors, `console errors:\n${consoleErrors.join("\n")}`).toEqual([]);
});
