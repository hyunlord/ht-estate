import { expect, test } from "@playwright/test";

// 학교 거리 섹션(school-1) — API mock(키리스). 가까운 초/중/고 + 거리·이름. computed-or-dash.

const base = {
  approval_date: "2020-01-01", parking_ratio: 1.5, parking_underground: 100, household_count: 300,
  lat: 37.5, lng: 127.04, source_url: "https://k-apt.example/x", transaction_count: 0,
  price_min: null, price_max: null, representative_trade: null,
};

const CANDIDATES = [
  {
    ...base, complex_id: "A1", name: "학교근처아파트",
    school: [
      { level: "elem", label: "초등학교", nearest_dist_m: 250, nearest_name: "행복초등학교",
        nearest_school_id: "S1", count_500m: 1, count_1km: 2 },
      { level: "mid", label: "중학교", nearest_dist_m: 420, nearest_name: "행복중학교",
        nearest_school_id: "S2", count_500m: 1, count_1km: 1 },
      { level: "high", label: "고등학교", nearest_dist_m: 700, nearest_name: "행복고등학교",
        nearest_school_id: "S3", count_500m: 0, count_1km: 1 },
    ],
  },
  { ...base, complex_id: "A2", name: "미계산아파트", school: [] }, // computed-or-dash
];

test("school distance section: nearest 초/중/고 + dist / none", async ({ page }) => {
  await page.route("**/complexes/search", (route) => route.fulfill({ json: CANDIDATES }));
  await page.route("**/complexes/markers", (route) => route.fulfill({ json: [] }));
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
  const items = page.getByTestId("result-item");
  await expect(items).toHaveCount(2);

  await items.nth(0).click();
  let card = page.getByTestId("complex-card");
  await expect(card.getByTestId("school-elem")).toContainText("행복초등학교");
  await expect(card.getByTestId("school-elem")).toContainText("250m");
  await expect(card.getByTestId("school-mid")).toContainText("행복중학교");
  await expect(card.getByTestId("school-high")).toContainText("700m");

  await items.nth(1).click();
  card = page.getByTestId("complex-card");
  await expect(card.getByTestId("school-status")).toContainText("정보 없음");
});
