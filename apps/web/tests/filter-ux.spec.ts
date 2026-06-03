import { expect, test } from "@playwright/test";

// 필터 UX(P4-3a-2) — (1) 범위 슬라이더 ↔ 숫자입력 동기 (2) 평/㎡ 토글이 면적 표기 전환(리스트·상세).
const CAND = {
  complex_id: "C1", name: "역삼자이", approval_date: "2016-06-22", parking_ratio: 1.5,
  parking_underground: 615, household_count: 408, lat: 37.5, lng: 127.04,
  source_url: "https://k-apt.example/C1", transaction_count: 1, price_min: 142000, price_max: 142000,
  gym: null, pet: null,
  representative_trade: {
    net_area: 84.97, price: 142000, deposit: null, monthly_rent: null, rent_type: null,
    floor: 12, deal_date: "2025-04-15", match_confidence: 1.0,
  },
  criteria_eval: [],
};

test("range slider ↔ number input sync", async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));
  page.on("pageerror", (e) => consoleErrors.push(e.message));
  await page.route("**/complexes/search", (route) => route.fulfill({ json: [CAND] }));
  await page.route("**/complexes/markers", (route) => route.fulfill({ json: [] }));

  await page.goto("/", { waitUntil: "networkidle" });

  // 가격 드롭다운: 숫자입력 ↔ 슬라이더 동일 state 바인딩(양방향 동기 — 한쪽 변경이 양쪽에 반영).
  await page.getByTestId("fdrop-price").click();
  await page.getByTestId("in-priceMin").fill("50000");
  await expect(page.getByTestId("slider-priceMin")).toHaveValue("50000");
  await page.getByTestId("in-priceMax").fill("180000");
  await expect(page.getByTestId("slider-priceMax")).toHaveValue("180000");

  expect(consoleErrors, consoleErrors.join("\n")).toEqual([]);
});

test("평/㎡ toggle switches area display in list and detail", async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));
  page.on("pageerror", (e) => consoleErrors.push(e.message));
  await page.route("**/complexes/search", (route) => route.fulfill({ json: [CAND] }));
  await page.route("**/complexes/markers", (route) => route.fulfill({ json: [] }));

  await page.goto("/", { waitUntil: "networkidle" });

  const item = page.getByTestId("result-item");
  // 기본 단위 = 평 → 84.97㎡ = 25.7평.
  await expect(item).toContainText("25.7평");

  // 전용면적 드롭다운 → ㎡ 토글 → 리스트 표기 ㎡로 전환.
  await page.getByTestId("fdrop-area").click();
  await page.getByTestId("unit-sqm").click();
  await expect(item).toContainText("84.97㎡");
  await page.getByTestId("fdrop-area").click(); // 팝오버 닫기(리스트 클릭 가림 방지)

  // 상세 패널도 ㎡.
  await item.click();
  await expect(page.getByTestId("complex-card").getByTestId("representative-trade")).toBeVisible();
  await expect(page.getByTestId("complex-card")).toContainText("84.97㎡");

  // 다시 평으로.
  await page.getByTestId("fdrop-area").click();
  await page.getByTestId("unit-pyeong").click();
  await expect(item).toContainText("25.7평");

  expect(consoleErrors, consoleErrors.join("\n")).toEqual([]);
});
