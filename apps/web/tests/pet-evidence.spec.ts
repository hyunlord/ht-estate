import { expect, test } from "./_criteria";

// pet-evidence — doc 교차검증(pet_verified) 결합 → 디테일 pet 행 **advisory** 표시.
// ★★ 안전 바닥: 하드 ✓ "가능" 절대 없음 · 항상 "관리사무소 확인 권장" + 견종/무게 단서 + 출처 딥링크.
// API mock(키리스). 검증/결합은 백엔드(synthesize_pet)·여긴 advisory 표시 계약만.

const base = {
  approval_date: "2018-01-01", parking_ratio: 1.2, parking_underground: 80, household_count: 200,
  lat: 37.5, lng: 127.04, source_url: "https://k-apt.example/x", transaction_count: 0,
  price_min: null, price_max: null, representative_trade: null,
};

const CANDIDATES = [
  { ...base, complex_id: "A1", name: "역삼자이",
    gym: { has_gym: "none", confidence: null, evidence: null, sources: [] } },
];

// pet_verified 결합 결과: allowed(가능) but advisory — 견종/무게 caveats + 검증 doc 출처.
const PET_ADVISORY = {
  status: "ready",
  summary: {
    pet_allowed: "yes",
    confidence: 0.78,
    evidence: "관리규약상 반려동물 사육 허용",
    caveats: ["20kg 이하", "맹견 제외"],
    confirm_with_office: true,
    sources: [{ source_type: "web_verified", source_url: "https://cafe.naver.com/x/1" }],
  },
};

async function open(page: import("@playwright/test").Page, pet: object) {
  await page.route("**/complexes/search", (route) => route.fulfill({ json: CANDIDATES }));
  await page.route("**/complexes/markers", (route) =>
    route.fulfill({ json: { mode: "markers", markers: [], clusters: [] } }));
  await page.route("**/enrichment", (route) =>
    route.fulfill({ json: { complex_id: "A1", gym: { status: "unavailable", summary: null }, pet } }));
  await page.goto("/", { waitUntil: "networkidle" });
  await page.getByTestId("result-item").nth(0).click();
  return page.getByTestId("complex-card");
}

test("pet-evidence: allowed → advisory(하드 ✓ 없음·관리사무소·견종/무게 단서·출처)", async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));
  page.on("pageerror", (e) => consoleErrors.push(e.message));

  const card = await open(page, PET_ADVISORY);

  // ★ 하드 ✓ 아님 — 상태는 advisory 텍스트("가능(확인 권장)"), ✓ 아이콘 아님.
  await expect(card.getByTestId("pet-status")).toContainText("확인 권장");
  await expect(card.getByTestId("pet-status")).not.toHaveText("✓");
  // 항상 관리사무소 확인 안내
  await expect(card.getByTestId("pet-advisory")).toContainText("관리사무소");
  // 견종/무게 단서 보존
  await expect(card.getByTestId("pet-caveat").first()).toContainText("20kg");
  // 검증 doc 출처 딥링크
  await expect(card.getByTestId("pet-source-link")).toHaveAttribute("href", "https://cafe.naver.com/x/1");

  expect(consoleErrors, consoleErrors.join("\n")).toEqual([]);
});

test("pet-evidence: 미확인(정보 없음) → 정직 표기, 하드 단정 0", async ({ page }) => {
  const card = await open(page, { status: "ready", summary: null });
  await expect(card.getByTestId("pet-status")).toHaveText("정보 없음 / 미조사");
  await expect(card.getByTestId("pet-source-link")).toHaveCount(0);
});
