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

test("filter-trim: 기본 칩 = 메이저만(+고정) · long-tail 칩 없음 · hard/soft 배선 · NL 안내", async ({
  page,
}) => {
  const bodies: Record<string, unknown>[] = [];
  await page.route("**/complexes/search", (route) => {
    bodies.push(route.request().postDataJSON() as Record<string, unknown>);
    route.fulfill({ json: CAND });
  });
  await page.route("**/complexes/markers", (route) => route.fulfill({ json: { mode: "markers", markers: [], clusters: [] } }));

  await page.goto("/", { waitUntil: "networkidle" });

  // ★ 메이저 칩만 기본 노출 — 메이저 hard(역세권 500m) 클릭 → subway_max_dist_m=500.
  await page.getByTestId("chip-subway_poi").click();
  await expect
    .poll(() => (bodies.at(-1) as { subway_max_dist_m?: number })?.subway_max_dist_m)
    .toBe(500);

  // 메이저 hard(세대당주차 1대+) → parking_ratio_gte=1.0.
  await page.getByTestId("chip-parking_q").click();
  await expect
    .poll(() => (bodies.at(-1) as { parking_ratio_gte?: number })?.parking_ratio_gte)
    .toBe(1.0);

  // 메이저 soft(헬스장) → soft.gym=preferred.
  await page.getByTestId("chip-gym_q").click();
  await expect
    .poll(() => (bodies.at(-1) as { soft?: { gym?: string } })?.soft?.gym)
    .toBe("preferred");

  // ★ long-tail 칩은 기본 노출 안 됨(어린이집·초등·편의점·CCTV·공원) — REGISTRY엔 있으나 칩 미렌더.
  for (const id of ["chip-has_daycare", "chip-elem_school", "chip-conv_poi", "chip-cctv", "chip-park_poi"]) {
    await expect(page.getByTestId(id)).toHaveCount(0);
  }

  // long-tail 도달 경로(NL 안내) 표면화.
  await expect(page.getByTestId("nl-hint")).toContainText("어린이집");
  await expect(page.getByTestId("nl-hint")).toContainText("공원");

  // ResultList 뱃지: criteria_eval 값 포맷은 그대로(칩 trim과 무관·표시 전용).
  const card = page.getByTestId("result-item").first();
  await expect(card).toContainText("초등학교 거리 75m");
  await expect(card).toContainText("편의점 12");
});
