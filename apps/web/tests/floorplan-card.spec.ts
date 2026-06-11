import { expect, test } from "./_criteria";

// floorplan 행 (P3-2) — API mock(키리스). 객관 feature(bay·향·판상/타워) · null-tolerant · 출처 ·
// gym/pet/review 공존. v2: 상세 패널(complex-card)에 행. 마운트 auto-search. **표시 전용**.

const base = {
  approval_date: "2020-01-01", parking_ratio: 1.5, parking_underground: 100, household_count: 300,
  lat: 37.5, lng: 127.04, source_url: "https://k-apt.example/x", transaction_count: 0,
  price_min: null, price_max: null, representative_trade: null,
  gym: { has_gym: "yes", confidence: 0.9, evidence: "피트니스", sources: [] },
  pet: { pet_allowed: "yes", confidence: 0.8, evidence: "허용", caveats: [],
    confirm_with_office: true, sources: [] },
  review: { summary: null, points: [], confidence: null, sources: [] },
};

const fp = (
  bay: number | null, orientation: string | null, structure: string | null,
  evidence: string | null, sources: { source_type: string; source_url: string }[],
) => ({ bay, orientation, structure, evidence, confidence: 0.5, sources });

const CANDIDATES = [
  {
    ...base, complex_id: "A1", name: "완전추출단지",
    floorplan: fp(3, "남향", "판상형", "전면 거실+침실2 나란히", [
      { source_type: "agent_research", source_url: "https://k-apt.example/fp/A1" },
    ]),
  },
  {
    ...base, complex_id: "A2", name: "부분추출단지",
    floorplan: fp(null, null, "타워형", "중앙 코어 ㅁ자", [
      { source_type: "agent_research", source_url: "urn:ht-estate:auto:A2" },
    ]),
  },
  { ...base, complex_id: "A3", name: "미조사단지", floorplan: fp(null, null, null, null, []) },
];

async function search(page: import("@playwright/test").Page) {
  await page.route("**/complexes/search", (route) => route.fulfill({ json: CANDIDATES }));
  await page.route("**/complexes/markers", (route) => route.fulfill({ json: { mode: "markers", markers: [], clusters: [] } }));
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
}

test("floorplan row: features / null-tolerant partial / none / link / coexist", async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));
  page.on("pageerror", (e) => consoleErrors.push(e.message));

  await search(page);
  const items = page.getByTestId("result-item");
  await expect(items).toHaveCount(3);

  await items.nth(0).click();
  let card = page.getByTestId("complex-card");
  await expect(card.getByTestId("floorplan-features")).toContainText("3bay");
  await expect(card.getByTestId("floorplan-features")).toContainText("남향");
  await expect(card.getByTestId("floorplan-features")).toContainText("판상형");
  await expect(card.getByTestId("floorplan-evidence")).toContainText("전면");
  await expect(card.getByTestId("floorplan-source-link")).toHaveAttribute(
    "href", "https://k-apt.example/fp/A1",
  );
  await expect(card.getByTestId("gym-row")).toBeVisible();
  await expect(card.getByTestId("pet-row")).toBeVisible();

  await items.nth(1).click();
  card = page.getByTestId("complex-card");
  await expect(card.getByTestId("floorplan-features")).toHaveText("타워형");
  await expect(card.getByTestId("floorplan-source-agent")).toBeVisible();
  await expect(card.getByTestId("floorplan-source-link")).toHaveCount(0);

  await items.nth(2).click();
  card = page.getByTestId("complex-card");
  await expect(card.getByTestId("floorplan-status")).toHaveText("정보 없음 / 미조사");
  await expect(card.getByTestId("floorplan-features")).toHaveCount(0);

  await items.nth(0).click();
  card = page.getByTestId("complex-card");
  await card.scrollIntoViewIfNeeded();
  await card.screenshot({ path: "test-results/floorplan-card.png" });
  expect(consoleErrors, consoleErrors.join("\n")).toEqual([]);
});
