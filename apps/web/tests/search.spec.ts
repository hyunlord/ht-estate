import { expect, test } from "@playwright/test";

// API를 mock해 키리스로 패널→검색→카드 플로우 검증(Kakao SDK 무관).
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
    floor: 12,
    deal_date: "2025-04-15",
    match_confidence: 0.6, // 저신뢰 → 추정 매칭 배지
  },
};

test("search flow: panel → API → card with badge and source link", async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));
  page.on("pageerror", (e) => consoleErrors.push(e.message));

  await page.route("**/complexes/search", async (route) => {
    await route.fulfill({ json: [CANDIDATE] });
  });

  await page.goto("/", { waitUntil: "networkidle" });
  await page.getByTestId("search-button").click();

  const item = page.getByTestId("result-item");
  await expect(item).toHaveText("역삼자이");
  await item.click();

  const card = page.getByTestId("complex-card");
  await expect(card).toBeVisible();
  await expect(card.getByTestId("estimated-match-badge")).toBeVisible();
  await expect(card.getByTestId("source-link")).toHaveAttribute(
    "href",
    "https://k-apt.example/C1",
  );
  await expect(card.getByTestId("representative-trade")).toContainText("142,000만원");

  expect(consoleErrors, consoleErrors.join("\n")).toEqual([]);
});
