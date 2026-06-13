import { expect, test } from "./_criteria";

// gym-evidence — Kakao 위치(kakao_local) + doc 교차검증(web_verified) 결합 표시.
// /enrichment gym 섹션이 결합 summary(증거+다출처) 반환 → 디테일 gym 행에 위치+증거 + 출처 딥링크 둘 다.
// API mock(키리스). 결합 로직은 백엔드(synthesize_gym)·여긴 표시 계약만.

const base = {
  approval_date: "2018-01-01", parking_ratio: 1.2, parking_underground: 80, household_count: 200,
  lat: 37.48, lng: 126.78, source_url: "https://k-apt.example/x", transaction_count: 0,
  price_min: null, price_max: null, representative_trade: null,
};

const CANDIDATES = [
  { ...base, complex_id: "KCC", name: "케이씨씨엠파이어타워",
    gym: { has_gym: "none", confidence: null, evidence: null, sources: [] } },
];

// 결합 결과: Kakao 위치(스포애니 13m) + doc 검증(단지 내 헬스장 후기), 두 독립신호 일치 → conf 부스트.
const GYM_COMBINED = {
  status: "ready",
  summary: {
    has_gym: "yes",
    confidence: 0.93,
    evidence: "스포애니 부천심곡점 (13m·Kakao Local) · 단지 내 헬스장 운영 후기",
    sources: [
      { source_type: "kakao_local", source_url: "http://place.map.kakao.com/123" },
      { source_type: "web_verified", source_url: "https://blog.naver.com/x/456" },
    ],
  },
};

test("gym-evidence: Kakao 위치 + doc 검증 결합 — 증거+출처 딥링크 둘 다", async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));
  page.on("pageerror", (e) => consoleErrors.push(e.message));

  await page.route("**/complexes/search", (route) => route.fulfill({ json: CANDIDATES }));
  await page.route("**/complexes/markers", (route) =>
    route.fulfill({ json: { mode: "markers", markers: [], clusters: [] } }));
  await page.route("**/enrichment", (route) =>
    route.fulfill({
      json: { complex_id: "KCC", gym: GYM_COMBINED, pet: { status: "unavailable", summary: null } },
    }));

  await page.goto("/", { waitUntil: "networkidle" });
  await page.getByTestId("result-item").nth(0).click();
  const card = page.getByTestId("complex-card");

  // ✓ 있음 + 결합 증거(위치 + 검증 후기 둘 다)
  await expect(card.getByTestId("gym-status")).toHaveText("✓");
  const evidence = card.getByTestId("gym-evidence");
  await expect(evidence).toContainText("스포애니"); // Kakao 위치
  await expect(evidence).toContainText("후기");      // doc 검증
  await expect(card.getByTestId("gym-row")).toContainText("conf 0.93");

  // 출처 딥링크 둘 다(Kakao place + 검증 doc)
  const links = card.getByTestId("gym-source-link");
  await expect(links).toHaveCount(2);
  await expect(links.nth(0)).toHaveAttribute("href", "http://place.map.kakao.com/123");
  await expect(links.nth(1)).toHaveAttribute("href", "https://blog.naver.com/x/456");

  expect(consoleErrors, consoleErrors.join("\n")).toEqual([]);
});
