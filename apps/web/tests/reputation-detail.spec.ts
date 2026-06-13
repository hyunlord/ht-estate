import { expect, test } from "./_criteria";

// 후기/평판 RAG 섹션(E3-3) — API mock(키리스). 프리셋 칩 클릭 → reputation 엔드포인트 →
// 종합 요약 + 인용 딥링크(source_url + span_ref) + advisory 배지. pending→폴링→ready.
// graceful: gemma degrade면 summary 없이 인용만(evidence-only).

const base = {
  approval_date: "2020-01-01", parking_ratio: 1.5, parking_underground: 100, household_count: 300,
  lat: 37.5, lng: 127.04, source_url: "https://k-apt.example/x", transaction_count: 0,
  price_min: null, price_max: null, representative_trade: null,
};

const CANDIDATES = [
  {
    ...base, complex_id: "A1", name: "헬리오시티",
    gym: { has_gym: "none", confidence: null, evidence: null, sources: [] },
    pet: { pet_allowed: "none", confidence: null, evidence: null, caveats: [],
      confirm_with_office: true, sources: [] },
  },
];

const REP_READY = {
  complex_id: "A1",
  status: "ready",
  summary: "주차가 넉넉하다는 평과 부족하다는 평이 함께 언급됨.",
  citations: [
    { source_type: "blog", source_url: "https://blog.example/1", span_ref: "p0", snippet: "주차 넉넉" },
    { source_type: "cafe", source_url: "https://cafe.example/2", span_ref: "p1", snippet: "주차 부족" },
  ],
  degraded: [],
};

test("reputation: 프리셋 칩 → pending 폴링 → 종합+인용 딥링크+advisory", async ({ page }) => {
  await page.route("**/complexes/search", (route) => route.fulfill({ json: CANDIDATES }));
  await page.route("**/complexes/markers", (route) => route.fulfill({ json: { mode: "markers", markers: [], clusters: [] } }));
  await page.route("**/enrichment", (route) =>
    route.fulfill({
      json: { complex_id: "A1", gym: { status: "ready", summary: null }, pet: { status: "ready", summary: null } },
    }),
  );
  let calls = 0;
  await page.route("**/reputation", (route) => {
    calls += 1;
    // 1차: 코퍼스 수집 중(pending) → 2차: ready(종합+인용)
    route.fulfill({
      json: calls === 1 ? { complex_id: "A1", status: "pending", summary: null, citations: [], degraded: [] } : REP_READY,
    });
  });

  await page.goto("/", { waitUntil: "networkidle" });
  const items = page.getByTestId("result-item");
  await expect(items).toHaveCount(1);
  await items.nth(0).click();

  const card = page.getByTestId("complex-card");
  // advisory 배지(단정 아님)
  await expect(card.getByTestId("reputation-advisory")).toBeVisible();

  // 프리셋 칩 클릭(주차)
  await card.getByTestId("reputation-chip").filter({ hasText: "주차" }).click();

  // 1차 pending → "수집/분석 중"
  await expect(card.getByTestId("reputation-pending")).toBeVisible();

  // 2차 ready → 종합 요약 + 인용 딥링크(source_url + span_ref)
  await expect(card.getByTestId("reputation-summary")).toContainText("주차");
  const cites = card.getByTestId("reputation-citation-link");
  await expect(cites.first()).toHaveAttribute("href", "https://blog.example/1");
  await expect(cites.first()).toContainText("p0"); // span_ref 정밀
});

test("reputation: gemma degrade → 인용만(evidence-only·summary 없음)", async ({ page }) => {
  await page.route("**/complexes/search", (route) => route.fulfill({ json: CANDIDATES }));
  await page.route("**/complexes/markers", (route) => route.fulfill({ json: { mode: "markers", markers: [], clusters: [] } }));
  await page.route("**/enrichment", (route) =>
    route.fulfill({
      json: { complex_id: "A1", gym: { status: "ready", summary: null }, pet: { status: "ready", summary: null } },
    }),
  );
  await page.route("**/reputation", (route) =>
    route.fulfill({
      json: {
        complex_id: "A1", status: "ready", summary: null,
        citations: [{ source_type: "blog", source_url: "https://blog.example/1", span_ref: "p0", snippet: "주차" }],
        degraded: ["synth"],
      },
    }),
  );

  await page.goto("/", { waitUntil: "networkidle" });
  await page.getByTestId("result-item").nth(0).click();
  const card = page.getByTestId("complex-card");
  await card.getByTestId("reputation-chip").filter({ hasText: "주차" }).click();

  // summary 없음 + 인용만 노출(crash 0·evidence-only)
  await expect(card.getByTestId("reputation-citation-link").first()).toBeVisible();
  await expect(card.getByTestId("reputation-summary")).toHaveCount(0);
});

// rag-corpus-quality: 코퍼스 0/thin(매치 0·인용 0) → 정직하게 "후기 미수집"(빈 "정보 없음" 아님).
test("reputation: 코퍼스 미수집 → '후기 미수집' 정직 표기(인용·요약 없음)", async ({ page }) => {
  await page.route("**/complexes/search", (route) => route.fulfill({ json: CANDIDATES }));
  await page.route("**/complexes/markers", (route) => route.fulfill({ json: { mode: "markers", markers: [], clusters: [] } }));
  await page.route("**/enrichment", (route) =>
    route.fulfill({
      json: { complex_id: "A1", gym: { status: "ready", summary: null }, pet: { status: "ready", summary: null } },
    }),
  );
  await page.route("**/reputation", (route) =>
    route.fulfill({
      json: { complex_id: "A1", status: "ready", summary: null, citations: [], degraded: [] },
    }),
  );

  await page.goto("/", { waitUntil: "networkidle" });
  await page.getByTestId("result-item").nth(0).click();
  const card = page.getByTestId("complex-card");
  await card.getByTestId("reputation-chip").filter({ hasText: "주차" }).click();

  // 정직한 미수집 표기 + 요약/인용 없음(빈 칩·"정보 없음" 아님)
  await expect(card.getByTestId("reputation-empty")).toContainText("미수집");
  await expect(card.getByTestId("reputation-summary")).toHaveCount(0);
  await expect(card.getByTestId("reputation-citation-link")).toHaveCount(0);
});
