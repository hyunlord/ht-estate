import { expect, test } from "@playwright/test";

// POI 근접(poi-1) — API mock(키리스). 디테일 카드 POI 섹션(computed-or-dash) + 역세권/마트 칩이
// hard 필터(subway_max_dist_m/mart_count_1km_min)를 request body로 보낸다.

const base = {
  approval_date: "2020-01-01", parking_ratio: 1.5, parking_underground: 100, household_count: 300,
  lat: 37.5, lng: 127.04, source_url: "https://k-apt/x", transaction_count: 0,
  price_min: null, price_max: null, representative_trade: null,
};
const CANDIDATES = [
  {
    ...base, complex_id: "A1", name: "역삼자이",
    poi: [
      { category: "SW8", label: "지하철역", nearest_dist_m: 320, nearest_name: "역삼역", count_500m: 1, count_1km: 2 },
      { category: "MT1", label: "대형마트", nearest_dist_m: 410, nearest_name: "이마트", count_500m: 2, count_1km: 5 },
    ],
  },
  { ...base, complex_id: "A2", name: "미계산단지", poi: [] }, // computed-or-dash
];

async function setup(page: import("@playwright/test").Page, captured: { body?: unknown }) {
  await page.route("**/complexes/search", (route) => {
    captured.body = route.request().postDataJSON();
    return route.fulfill({ json: CANDIDATES });
  });
  await page.route("**/complexes/markers", (route) => route.fulfill({ json: [] }));
  await page.route("**/enrichment", (route) =>
    route.fulfill({
      json: { complex_id: "x", gym: { status: "unavailable", summary: null }, pet: { status: "unavailable", summary: null } },
    }),
  );
  await page.goto("/", { waitUntil: "networkidle" });
}

test("POI card section: computed values + dash for un-computed", async ({ page }) => {
  const cap: { body?: unknown } = {};
  await setup(page, cap);
  const items = page.getByTestId("result-item");
  await expect(items).toHaveCount(2);

  await items.nth(0).click();
  let card = page.getByTestId("complex-card");
  await expect(card.getByTestId("poi-SW8")).toContainText("지하철역 320m");
  await expect(card.getByTestId("poi-MT1")).toContainText("대형마트 410m");

  await items.nth(1).click();
  card = page.getByTestId("complex-card");
  await expect(card.getByTestId("poi-status")).toContainText("정보 없음"); // computed-or-dash
});

test("역세권/마트 칩이 POI hard 필터를 request body로 보낸다", async ({ page }) => {
  const cap: { body?: unknown } = {};
  await setup(page, cap);
  await page.getByText("역세권 500m").click();
  await expect.poll(() => (cap.body as { subway_max_dist_m?: number })?.subway_max_dist_m).toBe(500);
  await page.getByText("마트 1km").click();
  await expect.poll(() => (cap.body as { mart_count_1km_min?: number })?.mart_count_1km_min).toBe(1);
});
