import { expect, test } from "@playwright/test";

// review 카드 행 (P3-1) — API mock(키리스). 요약+포인트+다출처 딥링크 · 미조사 상태 ·
// urn 비링크 · gym/pet 공존(회귀 0) · 콘솔에러 0. **표시 전용**(랭킹 무관 — 정렬 검증 아님).

const base = {
  approval_date: "2020-01-01",
  parking_ratio: 1.5,
  parking_underground: 100,
  household_count: 300,
  lat: 37.5,
  lng: 127.04,
  source_url: "https://k-apt.example/x",
  transaction_count: 0,
  price_min: null,
  price_max: null,
  representative_trade: null,
  gym: { has_gym: "yes", confidence: 0.9, evidence: "단지 내 피트니스", sources: [] },
  pet: {
    pet_allowed: "yes", confidence: 0.8, evidence: "관리규약 허용", caveats: [],
    confirm_with_office: true, sources: [],
  },
};

const review = (
  summary: string | null,
  points: string[],
  confidence: number | null,
  sources: { source_type: string; source_url: string }[],
) => ({ summary, points, confidence, sources });

const CANDIDATES = [
  {
    ...base,
    complex_id: "A1",
    name: "개포자이프레지던스",
    // 요약 + 포인트 + 다출처(youtube http + tistory http)
    review: review("전반적으로 조용하고 관리가 잘 된다는 평.", ["조용함", "관리 양호"], 0.5, [
      { source_type: "youtube", source_url: "https://youtube.com/watch?v=a" },
      { source_type: "blog", source_url: "https://tistory.com/b" },
    ]),
  },
  {
    ...base,
    complex_id: "A2",
    name: "에이전트조사단지",
    review: review("주차가 빠듯하다는 의견.", [], 0.3, [
      { source_type: "agent_research", source_url: "urn:ht-estate:auto:A2" },
    ]),
  },
  { ...base, complex_id: "A3", name: "미조사단지", review: review(null, [], null, []) },
];

async function search(page: import("@playwright/test").Page) {
  await page.route("**/complexes/search", (route) => route.fulfill({ json: CANDIDATES }));
  await page.goto("/", { waitUntil: "networkidle" });
  await page.getByTestId("search-button").click();
}

test("review row: summary+points / multi-source links / urn nonlink / none / gym·pet coexist", async ({
  page,
}) => {
  const consoleErrors: string[] = [];
  page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));
  page.on("pageerror", (e) => consoleErrors.push(e.message));

  await search(page);
  const items = page.getByTestId("result-item");
  await expect(items).toHaveCount(3);

  // A1 — 요약 + 포인트 + 다출처(2 http 링크) + gym/pet 공존(회귀 0).
  await items.nth(0).click();
  let card = page.getByTestId("complex-card");
  await expect(card.getByTestId("review-summary")).toContainText("조용하고 관리가 잘");
  await expect(card.getByTestId("review-points")).toContainText("조용함");
  await expect(card.getByTestId("review-source-link")).toHaveCount(2); // 다출처 딥링크
  await expect(card.getByTestId("review-source-link").first()).toHaveAttribute(
    "href",
    "https://youtube.com/watch?v=a",
  );
  await expect(card.getByTestId("gym-row")).toBeVisible(); // 공존
  await expect(card.getByTestId("pet-row")).toBeVisible();

  // A2 — urn 출처는 비링크('에이전트 조사'), 포인트 없음.
  await items.nth(1).click();
  card = page.getByTestId("complex-card");
  await expect(card.getByTestId("review-summary")).toContainText("주차가 빠듯");
  await expect(card.getByTestId("review-source-agent")).toBeVisible();
  await expect(card.getByTestId("review-source-link")).toHaveCount(0);

  // A3 — 미조사: '정보 없음 / 미조사', 출처 없음.
  await items.nth(2).click();
  card = page.getByTestId("complex-card");
  await expect(card.getByTestId("review-status")).toHaveText("정보 없음 / 미조사");
  await expect(card.getByTestId("review-source-link")).toHaveCount(0);
  await expect(card.getByTestId("review-source-agent")).toHaveCount(0);

  // 데모 스크린샷: 요약+포인트+다출처(A1).
  await items.nth(0).click();
  card = page.getByTestId("complex-card");
  await card.scrollIntoViewIfNeeded();
  await card.screenshot({ path: "test-results/review-card.png" });

  expect(consoleErrors, consoleErrors.join("\n")).toEqual([]);
});
