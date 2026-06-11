import { expect, test } from "./_criteria";

// detail-1: 다평형 건물 → 디테일 카드 평형별 브레이크다운(평형마다 한 줄: 전용·최근가+월·거래수).
// 단일평형 건물 → 한 줄(과분할 없음). 키리스 mock(area_buckets는 backend 집계 산출물).
const MULTI = {
  complex_id: "M1",
  name: "롯데캐슬엠파이어",
  approval_date: "2018-06-22",
  parking_ratio: 1.5,
  parking_underground: 615,
  household_count: 408,
  lat: 37.5,
  lng: 127.04,
  source_url: "https://k-apt.example/M1",
  transaction_count: 7,
  price_min: 174300,
  price_max: 350000,
  representative_trade: {
    net_area: 126.75,
    price: 245000,
    deposit: null,
    monthly_rent: null,
    rent_type: null,
    floor: 12,
    deal_date: "2026-04-22",
    match_confidence: 1.0,
  },
  area_buckets: [
    {
      net_area: 107.58,
      transaction_count: 4,
      recent_amount: 174300,
      recent_monthly_rent: null,
      recent_rent_type: null,
      recent_deal_date: "2025-09-30",
      amount_min: 174300,
      amount_max: 237000,
    },
    {
      net_area: 126.75,
      transaction_count: 2,
      recent_amount: 245000,
      recent_monthly_rent: null,
      recent_rent_type: null,
      recent_deal_date: "2026-04-22",
      amount_min: 240000,
      amount_max: 245000,
    },
    {
      net_area: 213.84,
      transaction_count: 1,
      recent_amount: 350000,
      recent_monthly_rent: null,
      recent_rent_type: null,
      recent_deal_date: "2025-09-26",
      amount_min: 350000,
      amount_max: 350000,
    },
  ],
  gym: null,
  pet: null,
};

const SINGLE = {
  ...MULTI,
  complex_id: "S1",
  name: "단일평형단지",
  area_buckets: [
    {
      net_area: 84.97,
      transaction_count: 3,
      recent_amount: 142000,
      recent_monthly_rent: null,
      recent_rent_type: null,
      recent_deal_date: "2025-04-15",
      amount_min: 138000,
      amount_max: 142000,
    },
  ],
};

test("multi-area building shows per-평형 breakdown rows", async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));
  page.on("pageerror", (e) => consoleErrors.push(e.message));

  await page.route("**/complexes/search", (route) => route.fulfill({ json: [MULTI] }));
  await page.route("**/complexes/markers", (route) => route.fulfill({ json: { mode: "markers", markers: [], clusters: [] } }));
  await page.route("**/enrichment", (route) =>
    route.fulfill({
      json: {
        complex_id: "x",
        gym: { status: "unavailable", summary: null },
        pet: { status: "unavailable", summary: null },
      },
    }),
  );
  await page.goto("/", { waitUntil: "networkidle" });

  await page.getByTestId("result-item").first().click();
  const card = page.getByTestId("complex-card");
  await expect(card).toBeVisible();

  const rows = card.getByTestId("area-bucket-row");
  await expect(rows).toHaveCount(3);
  // 평형순(작은→큰): 32.5평 → 38.3평 → 64.7평. 최근가(억) + 월 + 거래수 표면화.
  await expect(rows.nth(0).getByTestId("area-bucket-area")).toContainText("32.5평");
  await expect(rows.nth(0).getByTestId("area-bucket-amount")).toContainText("17.4억");
  await expect(rows.nth(0).getByTestId("area-bucket-amount")).toContainText("2025-09");
  await expect(rows.nth(0).getByTestId("area-bucket-count")).toHaveText("4건");
  await expect(rows.nth(2).getByTestId("area-bucket-area")).toContainText("64.7평");

  expect(consoleErrors, consoleErrors.join("\n")).toEqual([]);
});

test("single-area building shows one breakdown row (no over-split)", async ({ page }) => {
  await page.route("**/complexes/search", (route) => route.fulfill({ json: [SINGLE] }));
  await page.route("**/complexes/markers", (route) => route.fulfill({ json: { mode: "markers", markers: [], clusters: [] } }));
  await page.route("**/enrichment", (route) =>
    route.fulfill({
      json: {
        complex_id: "x",
        gym: { status: "unavailable", summary: null },
        pet: { status: "unavailable", summary: null },
      },
    }),
  );
  await page.goto("/", { waitUntil: "networkidle" });

  await page.getByTestId("result-item").first().click();
  const card = page.getByTestId("complex-card");
  await expect(card.getByTestId("area-bucket-row")).toHaveCount(1);
  await expect(card.getByTestId("area-bucket-row").getByTestId("area-bucket-count")).toHaveText(
    "3건",
  );
});
