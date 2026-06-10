import { expect, test } from "./_criteria";

// 전월세(P2-2) — 거래유형 세그먼트 → 요청 body deal_type · 가격 라벨 적응(가격↔보증금) · 카드 전월세 표기.
const SALE = {
  complex_id: "C1", name: "역삼자이", approval_date: "2016-06-22", parking_ratio: 1.5,
  parking_underground: 615, household_count: 408, lat: 37.5, lng: 127.04,
  source_url: "https://k-apt/C1", transaction_count: 1, price_min: 142000, price_max: 142000,
  gym: null, pet: null,
  representative_trade: {
    net_area: 84.97, price: 142000, deposit: null, monthly_rent: null, rent_type: null,
    floor: 12, deal_date: "2025-04-15", match_confidence: 1.0,
  },
};
const JEONSE = {
  ...SALE,
  representative_trade: {
    net_area: 84.97, price: null, deposit: 90000, monthly_rent: 0, rent_type: "jeonse",
    floor: 12, deal_date: "2025-04-18", match_confidence: 1.0,
  },
};
const MONTHLY = {
  ...SALE,
  representative_trade: {
    net_area: 59.92, price: null, deposit: 20000, monthly_rent: 120, rent_type: "monthly",
    floor: 7, deal_date: "2025-03-05", match_confidence: 1.0,
  },
};

test("deal_type segment: request body, adaptive price label, rent card display", async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));
  page.on("pageerror", (e) => consoleErrors.push(e.message));

  const dealTypesSeen: (string | undefined)[] = [];
  await page.route("**/complexes/search", async (route) => {
    const body = route.request().postDataJSON() as { deal_type?: string };
    dealTypesSeen.push(body?.deal_type);
    const json = body?.deal_type === "jeonse" ? [JEONSE]
      : body?.deal_type === "monthly" ? [MONTHLY] : [SALE];
    await route.fulfill({ json });
  });
  await page.route("**/complexes/markers", (route) => route.fulfill({ json: [] }));
  await page.route("**/enrichment", (route) =>
    route.fulfill({
      json: {
        complex_id: "x",
        gym: { status: "unavailable", summary: null },
        pet: { status: "unavailable", summary: null },
      },
    }),
  );

  await page.goto("/", { waitUntil: "networkidle" }); // 마운트 = 매매 자동조회

  // 1) 기본 매매: 가격 라벨 "가격", deal-type-sale pressed, 카드 매매가.
  await expect(page.getByTestId("deal-type-sale")).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByTestId("fdrop-price")).toContainText("가격");
  await page.getByTestId("result-item").first().click();
  await expect(page.getByTestId("representative-trade")).toContainText("142,000만원");

  // 2) 전세: 가격 라벨 "보증금"으로 적응 · body deal_type=jeonse · 카드 전세.
  await page.getByTestId("deal-type-jeonse").click();
  await expect(page.getByTestId("fdrop-price")).toContainText("보증금");
  await page.getByTestId("result-item").first().click();
  await expect(page.getByTestId("representative-trade")).toContainText("전세 90,000만원");

  // 3) 월세: 카드 월세.
  await page.getByTestId("deal-type-monthly").click();
  await page.getByTestId("result-item").first().click();
  await expect(page.getByTestId("representative-trade")).toContainText("월세 20,000/120만원");

  // 요청 body: 매매(마운트)=deal_type 미전송, 전세/월세는 명시.
  expect(dealTypesSeen).toEqual([undefined, "jeonse", "monthly"]);

  expect(consoleErrors, consoleErrors.join("\n")).toEqual([]);
});
