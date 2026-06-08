import { expect, test } from "@playwright/test";

// auto-viewport(★ v2 핵심) — 검색 버튼 없이 마운트 시 현 bbox로 자동 조회 → 리스트/마커 채움.
// 인프라 칩 → spec 매핑 · criteria_eval ✓/△/✗/○ 상세 · 색마커 범례 · 키-graceful · 콘솔 0.

const CAND = {
  complex_id: "C1", name: "역삼자이", approval_date: "2016-06-22", parking_ratio: 1.5,
  parking_underground: 615, household_count: 408, lat: 37.5, lng: 127.04,
  source_url: "https://k-apt.example/C1", transaction_count: 2, price_min: 98000, price_max: 142000,
  representative_trade: {
    net_area: 84.97, price: 142000, deposit: null, monthly_rent: null, rent_type: null,
    floor: 12, deal_date: "2025-04-15", match_confidence: 1.0,
  },
  gym: null, pet: null,
  criteria_eval: [
    { key: "has_daycare", label: "어린이집", value: true, score: 1.0, confidence: 1.0, status: "match" },
    { key: "subway_time", label: "역세권(지하철 도보)", value: "5~10분이내", score: 0.7, confidence: 1.0, status: "partial" },
    { key: "household_count", label: "세대수", value: 408, score: 0.2, confidence: null, status: "unknown" },
  ],
};
const CAND2 = { ...CAND, complex_id: "C2", name: "은마", lat: 37.501, lng: 127.06 };

test("auto-viewport: mount auto-search by bbox (no button) + chip→spec + criteria_eval card", async ({
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
  await page.route("**/complexes/markers", (route) => route.fulfill({ json: [CAND, CAND2] }));
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

  // 셸 + 키리스 graceful + 범례.
  await expect(page.getByTestId("map-container")).toBeVisible();
  await expect(page.getByTestId("map-placeholder")).toBeVisible();
  await expect(page.getByTestId("legend")).toBeVisible();

  // ★ 검색 버튼 없음 — 마운트 자동 조회로 리스트가 채워졌다.
  await expect(page.getByTestId("search-button")).toHaveCount(0);
  await expect(page.getByTestId("result-item")).toHaveCount(2);
  // 첫 요청(마운트)은 bbox로 자동 발사됨.
  const first = bodies[0] as Record<string, unknown>;
  expect(typeof first.min_lat).toBe("number");
  expect(typeof first.max_lng).toBe("number");

  // 인프라 칩: 어린이집(soft) + 지하주차(hard) → 요청 spec 매핑.
  await page.getByTestId("chip-has_daycare").click();
  await page.getByTestId("chip-underground").click();
  const body = bodies.at(-1) as {
    parking_underground?: boolean;
    soft?: { criteria?: { key: string; weight: number }[] };
  };
  expect(body.parking_underground).toBe(true);
  expect(body.soft?.criteria).toContainEqual({ key: "has_daycare", weight: 1 });

  // 카드 클릭 → 상세 패널 criteria_eval ✓/△/○ 근거.
  await page.getByTestId("result-item").first().click();
  const card = page.getByTestId("complex-card");
  await expect(card.getByTestId("criteria-eval")).toBeVisible();
  await expect(card.getByTestId("criteria-eval-row")).toHaveCount(3);
  const st = card.getByTestId("criteria-eval-status");
  await expect(st.nth(0)).toHaveText("✓");
  await expect(st.nth(1)).toHaveText("△");
  await expect(st.nth(2)).toHaveText("○");
  await expect(card.getByTestId("criteria-eval-source").first()).toHaveAttribute(
    "href", "https://k-apt.example/C1",
  );
  // 아웃링크(네이버/호갱노노).
  await expect(card.getByTestId("naver-link")).toBeVisible();
  await expect(card.getByTestId("hogangnono-link")).toBeVisible();

  await page.screenshot({ path: "test-results/auto-viewport.png", fullPage: true });
  expect(consoleErrors, consoleErrors.join("\n")).toEqual([]);
});
