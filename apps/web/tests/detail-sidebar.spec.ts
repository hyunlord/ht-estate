import { expect, test } from "./_criteria";

// detail-panel-sidebar: DetailPanel = 우측 docked 컬럼(오버레이/클립 아님) — 뷰포트 내·스크롤·
// 전 area_buckets 노출·리사이즈·패널 열리면 맵 줄어듦. 키리스(지도 픽셀은 키 필요 — 여긴 레이아웃).

const MULTI = {
  complex_id: "M1",
  name: "롯데캐슬엠파이어퍼스트클래스레지던스",
  approval_date: "2018-06-22",
  parking_ratio: 1.5,
  parking_underground: 615,
  household_count: 408,
  lat: 37.5,
  lng: 127.04,
  source_url: "https://k-apt.example/M1",
  transaction_count: 7,
  price_min: 174300,
  price_max: 350000,
  representative_trade: {
    net_area: 126.75, price: 245000, deposit: null, monthly_rent: null, rent_type: null,
    floor: 12, deal_date: "2026-04-22", match_confidence: 1.0,
  },
  area_buckets: [
    { net_area: 84.97, transaction_count: 4, recent_amount: 142000, recent_monthly_rent: null,
      recent_rent_type: null, recent_deal_date: "2025-09-30", amount_min: 138000, amount_max: 142000 },
    { net_area: 107.58, transaction_count: 3, recent_amount: 174300, recent_monthly_rent: null,
      recent_rent_type: null, recent_deal_date: "2026-01-10", amount_min: 174300, amount_max: 200000 },
    { net_area: 126.75, transaction_count: 2, recent_amount: 245000, recent_monthly_rent: null,
      recent_rent_type: null, recent_deal_date: "2026-04-22", amount_min: 240000, amount_max: 245000 },
    { net_area: 213.84, transaction_count: 1, recent_amount: 350000, recent_monthly_rent: null,
      recent_rent_type: null, recent_deal_date: "2025-09-26", amount_min: 350000, amount_max: 350000 },
  ],
  criteria_eval: [],
  gym: null,
  pet: null,
};

async function openPanel(page: import("@playwright/test").Page) {
  await page.route("**/complexes/search", (route) => route.fulfill({ json: [MULTI] }));
  await page.route("**/complexes/markers", (route) =>
    route.fulfill({ json: { mode: "markers", markers: [], clusters: [] } }),
  );
  await page.route("**/enrichment", (route) =>
    route.fulfill({
      json: {
        complex_id: "M1",
        gym: { status: "unavailable", summary: null },
        pet: { status: "unavailable", summary: null },
      },
    }),
  );
  await page.goto("/", { waitUntil: "networkidle" });
  await page.getByTestId("result-item").first().click();
  await expect(page.getByTestId("complex-card")).toBeVisible();
}

test("패널이 docked 컬럼으로 뷰포트 안에 들어옴(오버플로우 클립 0) + 전 평형 버킷 노출", async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));
  page.on("pageerror", (e) => consoleErrors.push(e.message));

  await openPanel(page);
  const card = page.getByTestId("complex-card");

  // 뷰포트 내 — 우측 밖으로 안 넘침(클립 0).
  const vw = page.viewportSize()!.width;
  const box = (await card.boundingBox())!;
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width).toBeLessThanOrEqual(vw + 1);

  // 제목·가격·전 섹션 보임.
  await expect(card.getByTestId("representative-trade")).toBeVisible();
  // 전 area_buckets(4개) 렌더(프론트 캡 0).
  await expect(card.getByTestId("area-bucket-row")).toHaveCount(4);
  await expect(card.getByTestId("area-buckets-note")).toContainText("MOLIT 실거래");

  // 스크롤 컨테이너 존재 + 실제 스크롤 가능(내용이 컬럼보다 김).
  const scrollable = await page.getByTestId("detail-scroll").evaluate(
    (el) => el.scrollHeight > el.clientHeight,
  );
  expect(scrollable).toBe(true);

  expect(consoleErrors, consoleErrors.join("\n")).toEqual([]);
});

test("패널 열리면 맵이 줄어듦(덮지 않고 컨테인드) + 닫으면 복원", async ({ page }) => {
  await openPanel(page);
  const mapBox1 = (await page.getByTestId("map-container").boundingBox())!;

  // 닫으면 맵이 다시 넓어짐.
  await page.getByTestId("detail-close").click();
  await expect(page.getByTestId("complex-card")).toBeHidden();
  const mapBox2 = (await page.getByTestId("map-container").boundingBox())!;
  expect(mapBox2.width).toBeGreaterThan(mapBox1.width); // 패널 닫힘 → 맵 복원(넓어짐)
});

test("좌측 엣지 드래그로 패널 너비 리사이즈", async ({ page }) => {
  await openPanel(page);
  const card = page.getByTestId("complex-card");
  const w0 = (await card.boundingBox())!.width;

  const handle = page.getByTestId("detail-resize");
  const hb = (await handle.boundingBox())!;
  // 핸들을 왼쪽으로 120px 드래그 → 패널 넓어짐(width = innerWidth - clientX).
  await page.mouse.move(hb.x + hb.width / 2, hb.y + hb.height / 2);
  await page.mouse.down();
  await page.mouse.move(hb.x - 120, hb.y + hb.height / 2, { steps: 8 });
  await page.mouse.up();

  const w1 = (await card.boundingBox())!.width;
  expect(w1).toBeGreaterThan(w0 + 60); // 유의미하게 넓어짐
});
