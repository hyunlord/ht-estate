import { expect, test } from "./_criteria";

// school-assignment — 배정 초등 텍스트 입력 → assigned_school을 검색 request body로 보낸다(positive-match).
// 카드/DetailPanel은 배정 이미 표시(advisory) — 여긴 필터 입력→spec 배선 검증.

const base = {
  approval_date: "2020-01-01", parking_ratio: 1.5, parking_underground: 100, household_count: 300,
  lat: 37.5, lng: 127.04, source_url: null, transaction_count: 0,
  price_min: null, price_max: null, representative_trade: null,
};
const CAND = [
  {
    ...base, complex_id: "A1", name: "아크로리버파크",
    assignment: [
      { zone_id: "Z1", zone_class: "0", school_id: "S1", school_name: "서울잠원초등학교", is_shared: false },
    ],
  },
];

test("배정 초등 입력 → assigned_school을 검색 body로 전송 + 배정 advisory 표시", async ({ page }) => {
  const bodies: Record<string, unknown>[] = [];
  await page.route("**/complexes/search", (route) => {
    bodies.push(route.request().postDataJSON() as Record<string, unknown>);
    route.fulfill({ json: CAND });
  });
  await page.route("**/complexes/markers", (route) => route.fulfill({ json: [] }));
  await page.route("**/enrichment", (route) =>
    route.fulfill({
      json: { complex_id: "A1", gym: { status: "unavailable", summary: null }, pet: { status: "unavailable", summary: null } },
    }),
  );

  await page.goto("/", { waitUntil: "networkidle" });

  // 배정 초등 드롭다운 → 학교명 입력 → 적용 → assigned_school 전송.
  await page.getByTestId("fdrop-assigned").click();
  await page.getByTestId("in-assigned-school").fill("서울잠원초");
  await page.getByTestId("apply-assigned").click();
  await expect
    .poll(() => (bodies.at(-1) as { assigned_school?: string })?.assigned_school)
    .toBe("서울잠원초");

  // 결과 카드 상세 → 배정 초등 섹션 + advisory 배지(교육청 확인).
  await page.getByTestId("result-item").first().click();
  const card = page.getByTestId("complex-card");
  await expect(card.getByTestId("assignment-schools")).toContainText("서울잠원초등학교");
  await expect(card.getByTestId("assignment-confirm-badge")).toBeVisible();
});
