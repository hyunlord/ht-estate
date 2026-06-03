import { expect, test } from "@playwright/test";

// 지도-퍼스트 셸(P4-3a) — API mock(키리스). 지도 캔버스 + 오버레이 필터/랭크 · 인프라 칩→spec ·
// criteria_eval ✓/△/○ 근거 카드 + K-apt 출처 · 랭크↔선택 동기 · 콘솔에러 0.
// 렌더된 지도/마커/클러스터 픽셀은 JS 키 필요 → 사람 시각 확인(여긴 구조·연결·게이트).

const CAND = {
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
    match_confidence: 1.0,
  },
  gym: null,
  pet: null,
  criteria_eval: [
    { key: "has_daycare", label: "어린이집", value: true, score: 1.0, confidence: 1.0, status: "match" },
    { key: "subway_time", label: "역세권(지하철 도보)", value: "5~10분이내", score: 0.7, confidence: 1.0, status: "partial" },
    { key: "household_count", label: "세대수", value: 408, score: 0.2, confidence: null, status: "unknown" },
  ],
};

const CAND2 = { ...CAND, complex_id: "C2", name: "은마", lat: 37.501, lng: 127.06 };

test("map-first shell: canvas + overlays + infra chips → spec + criteria_eval card + rank sync", async ({
  page,
}) => {
  const consoleErrors: string[] = [];
  page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));
  page.on("pageerror", (e) => consoleErrors.push(e.message));

  const bodies: Record<string, unknown>[] = [];
  await page.route("**/complexes/search", async (route) => {
    bodies.push(route.request().postDataJSON() as Record<string, unknown>);
    await route.fulfill({ json: [CAND, CAND2] });
  });

  await page.goto("/", { waitUntil: "networkidle" });

  // 지도-퍼스트 셸: 지도 캔버스 메인 + 키리스 placeholder + 오버레이 필터.
  await expect(page.getByTestId("app-root")).toBeVisible();
  await expect(page.getByTestId("map-container")).toBeVisible();
  await expect(page.getByTestId("map-placeholder")).toBeVisible();
  await expect(page.getByTestId("filter-panel")).toBeVisible();
  await expect(page.getByTestId("infra-chips")).toBeVisible();

  // 인프라 칩: 어린이집(soft) + 지하주차(hard) 켜고 검색 → 요청 spec 매핑 확인.
  await page.getByTestId("chip-has_daycare").click();
  await page.getByTestId("chip-underground").click();
  await page.getByTestId("search-button").click();
  await expect(page.getByTestId("result-item")).toHaveCount(2);

  const body = bodies.at(-1) as {
    parking_underground?: boolean;
    soft?: { criteria?: { key: string; weight: number }[] };
  };
  expect(body.parking_underground).toBe(true); // 지하주차 → hard
  expect(body.soft?.criteria).toContainEqual({ key: "has_daycare", weight: 1 }); // 어린이집 → soft

  // 랭크 리스트 ↔ 선택: 첫 결과 클릭 → 상세 카드 + criteria_eval 근거.
  await page.getByTestId("result-item").first().click();
  const card = page.getByTestId("complex-card");
  await expect(card).toBeVisible();

  const evalCard = card.getByTestId("criteria-eval");
  await expect(evalCard).toBeVisible();
  await expect(card.getByTestId("criteria-eval-row")).toHaveCount(3);
  // status → 아이콘: match ✓ · partial △ · unknown ○.
  const statuses = card.getByTestId("criteria-eval-status");
  await expect(statuses.nth(0)).toHaveText("✓");
  await expect(statuses.nth(1)).toHaveText("△");
  await expect(statuses.nth(2)).toHaveText("○");
  await expect(evalCard).toContainText("어린이집");
  // 출처 = 단지 K-apt 딥링크.
  await expect(card.getByTestId("criteria-eval-source").first()).toHaveAttribute(
    "href",
    "https://k-apt.example/C1",
  );

  await page.screenshot({ path: "test-results/map-first.png", fullPage: true });
  expect(consoleErrors, consoleErrors.join("\n")).toEqual([]);
});
