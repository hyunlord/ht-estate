import { expect, test } from "./_criteria";

// unit-type-catalog: 디테일이 /unit-types 병합으로 전 세대타입(거래+미거래·세대수) 표시.
// graceful: has_catalog=false면 candidate.area_buckets 폴백(거래된 평형만·현 거동·무회귀).

const CAND = {
  complex_id: "U1", name: "유닛타입단지", approval_date: "2018-01-01", parking_ratio: 1.4,
  parking_underground: 100, household_count: 300, lat: 37.5, lng: 127.04, source_url: null,
  transaction_count: 1, price_min: 90000, price_max: 90000, criteria_eval: [], gym: null, pet: null,
  representative_trade: {
    net_area: 59.9, price: 90000, deposit: null, monthly_rent: null, rent_type: null,
    floor: 5, deal_date: "2026-04-01", match_confidence: 1.0,
  },
  area_buckets: [
    { net_area: 59.9, transaction_count: 3, recent_amount: 90000, recent_monthly_rent: null,
      recent_rent_type: null, recent_deal_date: "2026-04-01", amount_min: 88000, amount_max: 92000 },
  ],
};

async function setup(page: import("@playwright/test").Page, unitTypes: object) {
  await page.route("**/complexes/search", (route) => route.fulfill({ json: [CAND] }));
  await page.route("**/complexes/markers", (route) =>
    route.fulfill({ json: { mode: "markers", markers: [], clusters: [] } }),
  );
  await page.route("**/enrichment", (route) =>
    route.fulfill({ json: { complex_id: "U1", gym: { status: "unavailable", summary: null },
      pet: { status: "unavailable", summary: null } } }),
  );
  await page.route("**/unit-types**", (route) => route.fulfill({ json: unitTypes }));
  await page.goto("/", { waitUntil: "networkidle" });
  await page.getByTestId("result-item").first().click();
  await expect(page.getByTestId("complex-card")).toBeVisible();
}

test("전체 세대타입(거래+미거래·세대수) 렌더", async ({ page }) => {
  await setup(page, {
    has_catalog: true,
    types: [
      { net_area: 59.9, household_count: 200, transaction_count: 3, recent_amount: 90000,
        recent_monthly_rent: null, recent_rent_type: null, recent_deal_date: "2026-04-01",
        amount_min: 88000, amount_max: 92000, traded: true },
      { net_area: 84.9, household_count: 100, transaction_count: 0, recent_amount: null,
        recent_monthly_rent: null, recent_rent_type: null, recent_deal_date: null, traded: false },
    ],
  });
  const card = page.getByTestId("complex-card");
  const rows = card.getByTestId("unit-type-row");
  await expect(rows).toHaveCount(2);
  // 거래 타입: 세대수 + 실거래가. 미거래 타입: 세대수 + "미거래".
  await expect(rows.nth(0).getByTestId("unit-type-households")).toContainText("200세대");
  await expect(rows.nth(1).getByTestId("unit-type-households")).toContainText("100세대");
  await expect(rows.nth(1).getByTestId("unit-type-untraded")).toContainText("미거래");
  await expect(card.getByTestId("unit-types-note")).toContainText("전체 세대타입");
  // 폴백 area-buckets는 안 뜸(catalog 우선).
  await expect(card.getByTestId("area-buckets")).toHaveCount(0);
});

test("graceful: has_catalog=false면 area_buckets 폴백(현 거동)", async ({ page }) => {
  await setup(page, { has_catalog: false, types: [] });
  const card = page.getByTestId("complex-card");
  // catalog 없음 → 기존 "평형별 실거래"(area-buckets) 표시·전체 세대타입 안 뜸.
  await expect(card.getByTestId("area-buckets")).toBeVisible();
  await expect(card.getByTestId("area-buckets-note")).toContainText("거래된 평형만");
  await expect(card.getByTestId("unit-types")).toHaveCount(0);
});
