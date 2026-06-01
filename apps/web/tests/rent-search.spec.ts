import { expect, test } from "@playwright/test";

// 전월세 검색 UI(P2-2) — deal_type 토글·적응형 입력·요청 body deal_type·카드 전월세 표시.
// API mock(키리스): deal_type에 따라 적절한 rep(매매 price / 전세 deposit / 월세 deposit+monthly).

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
  ...SALE, price_min: 90000, price_max: 90000,
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

test("deal_type toggle: adaptive inputs, request body, rent card display", async ({ page }) => {
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

  await page.goto("/", { waitUntil: "networkidle" });

  // 1) 기본 = 매매: 가격 입력 보이고 보증금 입력 없음. deal-type-sale pressed.
  await expect(page.getByTestId("deal-type-sale")).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByText("가격 최소(만원)")).toBeVisible();
  await expect(page.getByText("보증금 최소(만원)")).toHaveCount(0);
  await page.getByTestId("search-button").click();
  await page.getByTestId("result-item").click();
  await expect(page.getByTestId("representative-trade")).toContainText("142,000만원");

  // 2) 전세: 보증금 입력 등장·가격 사라짐. 검색 → body deal_type=jeonse, 카드 전세 표시.
  await page.getByTestId("deal-type-jeonse").click();
  await expect(page.getByText("보증금 최소(만원)")).toBeVisible();
  await expect(page.getByText("가격 최소(만원)")).toHaveCount(0);
  await page.getByTestId("search-button").click();
  await page.getByTestId("result-item").click();
  await expect(page.getByTestId("representative-trade")).toContainText("전세 90,000만원");

  // 3) 월세: 보증금+월세 입력. 카드 월세 표시.
  await page.getByTestId("deal-type-monthly").click();
  await expect(page.getByText("월세 최소(만원)")).toBeVisible();
  await page.getByTestId("search-button").click();
  await page.getByTestId("result-item").click();
  await expect(page.getByTestId("representative-trade")).toContainText("월세 20,000/120만원");

  // 요청 body: 매매는 deal_type 미전송(undefined), 전세/월세는 명시.
  expect(dealTypesSeen).toEqual([undefined, "jeonse", "monthly"]);

  await page.getByTestId("deal-type-jeonse").click();
  await page.getByTestId("search-button").click();
  await page.getByTestId("result-item").click();
  await page.getByTestId("complex-card").scrollIntoViewIfNeeded();
  await page.getByTestId("complex-card").screenshot({ path: "test-results/rent-search.png" });

  expect(consoleErrors, consoleErrors.join("\n")).toEqual([]);
});
