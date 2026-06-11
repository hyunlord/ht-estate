import { expect, test } from "./_criteria";

// reputation-routing — 검색 NL이 주관 평판 의도(reputation_query)를 반환하면 (a) 감지 표식 칩,
// (b) 단지 detail 열 때 평판 섹션 자동 트리거(E3 RAG·detail-lazy). 검색 경로 인라인 synth 0.

const base = {
  approval_date: "2020-01-01", parking_ratio: 1.5, parking_underground: 100, household_count: 300,
  lat: 37.5, lng: 127.04, source_url: null, transaction_count: 0,
  price_min: null, price_max: null, representative_trade: null,
  gym: null, pet: null,
};
const CAND = [{ ...base, complex_id: "A1", name: "테스트단지" }];

const NL_RESP = {
  spec: { limit: 100, net_area_min: 84, soft: { gym: "none", pet: "none", criteria: [] } },
  detected: [{ phrase: "84", criterion_key: "net_area", mode: "hard" }],
  unsupported: [],
  candidates: CAND,
  reputation_query: "관리 잘 되는 조용한",
};

const REP_READY = {
  complex_id: "A1", status: "ready", summary: "관리 양호·정온하다는 평이 회자됨.",
  citations: [{ source_type: "blog", source_url: "https://blog.example/1", span_ref: "p0", snippet: "관리 좋음" }],
  degraded: [],
};

test("NL 평판 의도 → 감지 표식 + detail 평판 섹션 자동 트리거", async ({ page }) => {
  await page.route("**/complexes/search", (route) => route.fulfill({ json: CAND }));
  await page.route("**/complexes/markers", (route) => route.fulfill({ json: { mode: "markers", markers: [], clusters: [] } }));
  await page.route("**/complexes/search/nl", (route) => route.fulfill({ json: NL_RESP }));
  await page.route("**/enrichment", (route) =>
    route.fulfill({ json: { complex_id: "A1", gym: { status: "ready", summary: null }, pet: { status: "ready", summary: null } } }),
  );
  let repCalls = 0;
  let repQuery: string | null = null;
  await page.route("**/reputation", (route) => {
    repCalls += 1;
    repQuery = (route.request().postDataJSON() as { query?: string })?.query ?? null;
    route.fulfill({ json: REP_READY });
  });

  await page.goto("/", { waitUntil: "networkidle" });

  // NL 검색 — 평판 의도 포함 질의.
  await page.getByTestId("nl-search").fill("관리 잘 되는 조용한 84");
  await page.getByTestId("nl-search").press("Enter");

  // (a) 감지 표식: 평판 칩 노출(필터 칩과 구분).
  await expect(page.getByTestId("nl-reputation")).toContainText("관리 잘 되는 조용한");
  // 검색 경로선 reputation synth 호출 0(detail 열기 전).
  expect(repCalls).toBe(0);

  // (b) 단지 detail 열기 → 평판 섹션 자동 트리거(pre-seed reputation_query로 ask).
  await page.getByTestId("result-item").first().click();
  const card = page.getByTestId("complex-card");
  await expect(card.getByTestId("reputation-summary")).toContainText("관리 양호");
  await expect.poll(() => repQuery).toBe("관리 잘 되는 조용한"); // 자동 트리거 쿼리 = NL 평판 의도
  await expect(card.getByTestId("reputation-citation-link").first()).toBeVisible();
});

test("순수 구조 NL → 평판 표식 없음(false 라우팅 0)", async ({ page }) => {
  await page.route("**/complexes/search", (route) => route.fulfill({ json: CAND }));
  await page.route("**/complexes/markers", (route) => route.fulfill({ json: { mode: "markers", markers: [], clusters: [] } }));
  await page.route("**/complexes/search/nl", (route) =>
    route.fulfill({
      json: {
        spec: { limit: 100, net_area_min: 84, soft: { gym: "none", pet: "none", criteria: [] } },
        detected: [{ phrase: "84", criterion_key: "net_area", mode: "hard" }],
        unsupported: [], candidates: CAND, reputation_query: null,
      },
    }),
  );

  await page.goto("/", { waitUntil: "networkidle" });
  await page.getByTestId("nl-search").fill("전용 84 이상");
  await page.getByTestId("nl-search").press("Enter");
  await expect(page.getByTestId("result-item")).toHaveCount(1);
  await expect(page.getByTestId("nl-reputation")).toHaveCount(0); // 평판 표식 없음
});
