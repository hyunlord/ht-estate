import { expect, test } from "./_criteria";

// detail-panel-polish: ② 딜타입 전환시 열린 패널 즉시 갱신 · ③ 토글 가로(wrap 0) · ⑤ pet 행 제거.

const BASE = {
  complex_id: "C1", name: "테스트단지", approval_date: "2018-01-01", parking_ratio: 1.4,
  parking_underground: 100, household_count: 500, lat: 37.5, lng: 127.04, source_url: null,
  transaction_count: 1, price_min: 142000, price_max: 142000, criteria_eval: [], gym: null, pet: null,
};
const SALE = {
  ...BASE,
  representative_trade: {
    net_area: 84.97, price: 142000, deposit: null, monthly_rent: null, rent_type: null,
    floor: 5, deal_date: "2026-04-01", match_confidence: 1.0,
  },
};
const MONTHLY = {
  ...BASE,
  representative_trade: {
    net_area: 84.97, price: null, deposit: 20000, monthly_rent: 120, rent_type: "monthly",
    floor: 5, deal_date: "2026-04-01", match_confidence: 1.0,
  },
};

test("③ 딜타입 토글이 한 줄 가로(라벨 세로 wrap 0)", async ({ page }) => {
  await page.route("**/complexes/search", (route) => route.fulfill({ json: [SALE] }));
  await page.route("**/complexes/markers", (route) =>
    route.fulfill({ json: { mode: "markers", markers: [], clusters: [] } }),
  );
  await page.goto("/", { waitUntil: "networkidle" });

  const sale = (await page.getByTestId("deal-type-sale").boundingBox())!;
  const jeonse = (await page.getByTestId("deal-type-jeonse").boundingBox())!;
  const monthly = (await page.getByTestId("deal-type-monthly").boundingBox())!;
  // 세 버튼이 같은 행(top y 동일·가로 배치) — wrap이면 y가 어긋남.
  expect(Math.abs(sale.y - jeonse.y)).toBeLessThan(2);
  expect(Math.abs(sale.y - monthly.y)).toBeLessThan(2);
  // 가로로 나란히(x 증가).
  expect(jeonse.x).toBeGreaterThan(sale.x);
  expect(monthly.x).toBeGreaterThan(jeonse.x);
  // 라벨 한 줄(버튼 높이가 2줄로 안 늘어남 — 한 줄 텍스트 ~30px 내).
  expect(sale.height).toBeLessThan(40);
});

test("② 단지 선택 후 매매→월세 전환시 우측 패널 hero 즉시 갱신 + ⑤ pet 행 없음", async ({ page }) => {
  await page.route("**/complexes/search", (route) => {
    const body = route.request().postDataJSON() as { deal_type?: string };
    route.fulfill({ json: [body?.deal_type === "monthly" ? MONTHLY : SALE] });
  });
  await page.route("**/complexes/markers", (route) =>
    route.fulfill({ json: { mode: "markers", markers: [], clusters: [] } }),
  );
  await page.route("**/enrichment", (route) =>
    route.fulfill({
      json: {
        complex_id: "C1",
        gym: { status: "unavailable", summary: null },
        pet: { status: "unavailable", summary: null },
      },
    }),
  );
  await page.goto("/", { waitUntil: "networkidle" });

  // 매매 선택 → hero 매매가.
  await page.getByTestId("result-item").first().click();
  const card = page.getByTestId("complex-card");
  await expect(card.getByTestId("representative-trade")).toContainText("142,000만원");
  // pet-evidence: pet 행 재추가(advisory) — unavailable+무fallback → '정보 없음'·하드 ✓ 없음.
  await expect(card.getByTestId("pet-row")).toBeVisible();
  await expect(card.getByTestId("pet-status")).toHaveText("정보 없음 / 미조사");

  // 월세로 전환 — 패널을 다시 클릭하지 않아도 reconcile로 hero 갱신.
  await page.getByTestId("deal-type-monthly").click();
  await expect(page.getByTestId("complex-card").getByTestId("representative-trade")).toContainText(
    "보증금 2억 · 월세 120만원",
  );
});
