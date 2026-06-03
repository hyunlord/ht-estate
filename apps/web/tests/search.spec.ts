import { expect, test } from "@playwright/test";

// auto-viewport(마운트 자동검색) → 리스트 → 카드 클릭 → 상세 패널. 배지·출처·대표거래(키리스 mock).
const CANDIDATE = {
  complex_id: "C1",
  name: "역삼자이",
  approval_date: "2016-06-22",
  parking_ratio: 1.5,
  parking_underground: 615,
  household_count: 408,
  lat: 37.5,
  lng: 127.04,
  source_url: "https://k-apt.example/C1",
  transaction_count: 2,
  price_min: 98000,
  price_max: 142000,
  representative_trade: {
    net_area: 84.97,
    price: 142000,
    deposit: null,
    monthly_rent: null,
    rent_type: null,
    floor: 12,
    deal_date: "2025-04-15",
    match_confidence: 0.6, // 저신뢰 → 추정 매칭 배지
  },
  gym: null,
  pet: null,
};

test("auto-search → list → detail panel with badge and source link", async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));
  page.on("pageerror", (e) => consoleErrors.push(e.message));

  await page.route("**/complexes/search", (route) => route.fulfill({ json: [CANDIDATE] }));
  await page.route("**/complexes/markers", (route) => route.fulfill({ json: [] }));

  // 검색 버튼 없음 — 마운트 시 자동 조회로 리스트가 채워진다.
  await page.goto("/", { waitUntil: "networkidle" });

  const item = page.getByTestId("result-item");
  await expect(item).toHaveText(/역삼자이/);
  await item.click();

  const card = page.getByTestId("complex-card");
  await expect(card).toBeVisible();
  await expect(card.getByTestId("estimated-match-badge")).toBeVisible();
  await expect(card.getByTestId("source-link")).toHaveAttribute("href", "https://k-apt.example/C1");
  await expect(card.getByTestId("representative-trade")).toContainText("142,000만원");

  expect(consoleErrors, consoleErrors.join("\n")).toEqual([]);
});
