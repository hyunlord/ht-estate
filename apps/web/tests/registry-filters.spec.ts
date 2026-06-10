import { expect, test } from "./_criteria";

// frontend-polish-1 — TopBar 퀵 토글이 registry-driven(/criteria)으로 빌드되고, 신규 신호(학교거리·
// POI)가 등장·올바른 hard/soft 필드로 검색 body에 실린다. + ResultList 뱃지 값 포맷(초등 75m).

const base = {
  approval_date: "2020-01-01", parking_ratio: 1.5, parking_underground: 100, household_count: 300,
  lat: 37.5, lng: 127.04, source_url: null, transaction_count: 0,
  price_min: null, price_max: null, representative_trade: null,
};
const CAND = [
  {
    ...base, complex_id: "C1", name: "초품아단지",
    criteria_eval: [
      { key: "elem_dist", label: "초등학교 거리", value: 75, score: 0.93, confidence: 1.0, status: "match" },
      { key: "conv", label: "편의점", value: 12, score: 0.8, confidence: 1.0, status: "match" },
    ],
  },
];

test("registry-driven 퀵 토글: 신규 신호 등장 + hard/soft 배선 + 뱃지 값 포맷", async ({ page }) => {
  const bodies: Record<string, unknown>[] = [];
  await page.route("**/complexes/search", (route) => {
    bodies.push(route.request().postDataJSON() as Record<string, unknown>);
    route.fulfill({ json: CAND });
  });
  await page.route("**/complexes/markers", (route) => route.fulfill({ json: [] }));

  await page.goto("/", { waitUntil: "networkidle" });

  // 신규 학교거리 칩이 /criteria서 등장 → 클릭 → hard 필드(elem_max_dist_m=500) 전송.
  await page.getByTestId("chip-elem_school").click();
  await expect
    .poll(() => (bodies.at(-1) as { elem_max_dist_m?: number })?.elem_max_dist_m)
    .toBe(500);

  // 신규 POI 칩(편의점 1km) → conv_count_1km_min=1 (higher_better count).
  await page.getByTestId("chip-conv_poi").click();
  await expect
    .poll(() => (bodies.at(-1) as { conv_count_1km_min?: number })?.conv_count_1km_min)
    .toBe(1);

  // soft 토글(어린이집) → soft.criteria 푸시(hard 아님).
  await page.getByTestId("chip-has_daycare").click();
  await expect
    .poll(() =>
      (bodies.at(-1) as { soft?: { criteria?: { key: string }[] } })?.soft?.criteria?.map(
        (c) => c.key,
      ),
    )
    .toContain("has_daycare");

  // ResultList 뱃지: registry-driven 값 포맷 — 거리(lower_better)=m, 개수(higher_better)=숫자.
  const card = page.getByTestId("result-item").first();
  await expect(card).toContainText("초등학교 거리 75m");
  await expect(card).toContainText("편의점 12");
});
