import { expect, test } from "./_criteria";

// gym 행 — API mock(키리스). ✓/△/✗ 매핑 · evidence · conf · http 링크 / urn 비링크 / none.
// v2: 상세 패널(complex-card)에 행이 들어감. 마운트 auto-search로 리스트 채움(검색 버튼 없음).

const base = {
  approval_date: "2020-01-01", parking_ratio: 1.5, parking_underground: 100, household_count: 300,
  lat: 37.5, lng: 127.04, source_url: "https://k-apt.example/x", transaction_count: 0,
  price_min: null, price_max: null, representative_trade: null,
};

const CANDIDATES = [
  {
    ...base, complex_id: "A1", name: "디에이치퍼스티어아이파크",
    gym: { has_gym: "yes", confidence: 0.9, evidence: "단지 내 약 340평 피트니스(공식홈)",
      sources: [{ source_type: "official", source_url: "https://the-h.co.kr/x" }] },
  },
  {
    ...base, complex_id: "A2", name: "은마",
    gym: { has_gym: "no", confidence: 0.6, evidence: "단지 내 시설 없음 — 인근 상업 헬스장뿐",
      sources: [{ source_type: "agent_research", source_url: "urn:ht-estate:c4-agent:A2" }] },
  },
  {
    ...base, complex_id: "A3", name: "역삼자이아파트",
    gym: { has_gym: "unknown", confidence: 0.3, evidence: "단지내 단정 불가(노이즈)",
      sources: [{ source_type: "agent_research", source_url: "urn:ht-estate:c4-agent:A3" }] },
  },
  { ...base, complex_id: "A4", name: "미조사단지",
    gym: { has_gym: "none", confidence: null, evidence: null, sources: [] } },
];

async function search(page: import("@playwright/test").Page) {
  await page.route("**/complexes/search", (route) => route.fulfill({ json: CANDIDATES }));
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
  await page.goto("/", { waitUntil: "networkidle" }); // 마운트 auto-search가 리스트 채움
}

test("gym row maps status / shows evidence+conf / http-link / urn-nonlink / none", async ({
  page,
}) => {
  const consoleErrors: string[] = [];
  page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));
  page.on("pageerror", (e) => consoleErrors.push(e.message));

  await search(page);
  const items = page.getByTestId("result-item");
  await expect(items).toHaveCount(4);

  await items.nth(0).click();
  let card = page.getByTestId("complex-card");
  await expect(card.getByTestId("gym-status")).toHaveText("✓");
  await expect(card.getByTestId("gym-evidence")).toContainText("피트니스");
  await expect(card.getByTestId("gym-row")).toContainText("conf 0.90");
  const link = card.getByTestId("gym-source-link");
  await expect(link).toHaveAttribute("href", "https://the-h.co.kr/x");
  await expect(link).toHaveAttribute("target", "_blank");
  await expect(link).toHaveAttribute("rel", /noopener/);
  await expect(card.getByTestId("gym-source-agent")).toHaveCount(0);

  await items.nth(1).click();
  card = page.getByTestId("complex-card");
  await expect(card.getByTestId("gym-status")).toHaveText("✗");
  await expect(card.getByTestId("gym-source-agent")).toBeVisible();
  await expect(card.getByTestId("gym-source-link")).toHaveCount(0);

  await items.nth(2).click();
  card = page.getByTestId("complex-card");
  await expect(card.getByTestId("gym-status")).toHaveText("△");

  await items.nth(3).click();
  card = page.getByTestId("complex-card");
  await expect(card.getByTestId("gym-status")).toHaveText("정보 없음 / 미조사");
  await expect(card.getByTestId("gym-source-link")).toHaveCount(0);
  await expect(card.getByTestId("gym-source-agent")).toHaveCount(0);

  await items.nth(0).click();
  card = page.getByTestId("complex-card");
  await card.scrollIntoViewIfNeeded();
  await card.screenshot({ path: "test-results/gym-card.png" });
  expect(consoleErrors, consoleErrors.join("\n")).toEqual([]);
});
