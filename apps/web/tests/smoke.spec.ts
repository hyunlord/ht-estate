import { expect, test } from "./_criteria";


/**
 * 앱 셸 스모크 — v2 지도-퍼스트 (키리스).
 * 셸 렌더(상단 필터바·인프라 칩·좌측 리스트·지도·범례) + 키 부재 graceful(placeholder)
 * + 콘솔 에러 0 + 스크린샷. 라이브 지도/마커는 사람 시각 확인(JS 키 필요).
 */
test("app shell renders keyless with zero console errors", async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(msg.text());
  });
  page.on("pageerror", (err) => consoleErrors.push(err.message));

  // 마운트 시 auto-viewport 검색 + 마커 피드 발사 → 둘 다 mock(네트워크 에러/콘솔오염 방지).
  await page.route("**/complexes/search", (route) => route.fulfill({ json: [] }));
  await page.route("**/complexes/markers", (route) => route.fulfill({ json: { mode: "markers", markers: [], clusters: [] } }));

  await page.goto("/", { waitUntil: "networkidle" });

  await expect(page.getByTestId("app-root")).toBeVisible();
  await expect(page.getByTestId("deal-type")).toBeVisible();
  await expect(page.getByTestId("infra-chips")).toBeVisible();
  await expect(page.getByTestId("results")).toBeVisible();
  await expect(page.getByTestId("legend")).toBeVisible();
  await expect(page.getByTestId("map-container")).toBeVisible();
  await expect(page.getByTestId("map-placeholder")).toBeVisible(); // 키 부재 graceful
  await expect(page.getByTestId("results-empty")).toBeVisible(); // 빈 결과 안내

  await page.screenshot({ path: "test-results/home.png", fullPage: true });
  expect(consoleErrors, `console errors:\n${consoleErrors.join("\n")}`).toEqual([]);
});
