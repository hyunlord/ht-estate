import { expect, test } from "@playwright/test";

// pet 카드 행 — API mock(키리스). 5상태 시각구분 · conditional caveats · 확인권고 배지 전수 ·
// http 링크 / urn 비링크 · gym 공존. (P1-2b gym 패턴 재사용)

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
};

const pet = (
  pet_allowed: string,
  confidence: number | null,
  evidence: string | null,
  caveats: string[],
  source_url: string | null,
) => ({
  pet_allowed,
  confidence,
  evidence,
  caveats,
  confirm_with_office: true,
  sources: source_url ? [{ source_type: "x", source_url }] : [],
});

const CANDIDATES = [
  {
    ...base,
    complex_id: "A1",
    name: "개포자이프레지던스",
    // gym 공존 확인용
    gym: { has_gym: "yes", confidence: 0.9, evidence: "단지 내 피트니스", sources: [] },
    pet: pet("conditional", 0.55, "인식표·외부 출입 제한", ["입주민 인식표 의무", "외부 출입 제한"],
      "https://www.mk.co.kr/x"),
  },
  { ...base, complex_id: "A2", name: "허용단지", pet: pet("yes", 0.8, "관리규약 허용", [], "https://o/2") },
  { ...base, complex_id: "A3", name: "금지단지", pet: pet("no", 0.6, "금지 명시", [], "urn:ht-estate:c5-agent:A3") },
  { ...base, complex_id: "A4", name: "불명단지", pet: pet("unknown", 0.2, "공개 신호 없음", [], "urn:ht-estate:c5-agent:A4") },
  { ...base, complex_id: "A5", name: "미조사단지", pet: pet("none", null, null, [], null) },
];

async function search(page: import("@playwright/test").Page) {
  await page.route("**/complexes/search", (route) => route.fulfill({ json: CANDIDATES }));
  await page.goto("/", { waitUntil: "networkidle" });
  await page.getByTestId("search-button").click();
}

test("pet row: 5 states / caveats / confirm badge on all / links / gym coexist", async ({
  page,
}) => {
  const consoleErrors: string[] = [];
  page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));
  page.on("pageerror", (e) => consoleErrors.push(e.message));

  await search(page);
  const items = page.getByTestId("result-item");
  await expect(items).toHaveCount(5);

  // A1 개포자이 — conditional △조건부 + caveats + http 링크 + gym 공존 + 확인권고.
  await items.nth(0).click();
  let card = page.getByTestId("complex-card");
  await expect(card.getByTestId("pet-status")).toContainText("△");
  await expect(card.getByTestId("pet-status")).toContainText("조건부");
  await expect(card.getByTestId("pet-caveats")).toContainText("입주민 인식표 의무");
  await expect(card.getByTestId("pet-confirm-badge")).toBeVisible(); // 확인권고
  const petLink = card.getByTestId("pet-source-link");
  await expect(petLink).toHaveAttribute("href", "https://www.mk.co.kr/x");
  await expect(petLink).toHaveAttribute("rel", /noopener/);
  await expect(card.getByTestId("gym-row")).toBeVisible(); // gym 공존(회귀 0)
  await expect(card.getByTestId("gym-status")).toHaveText("✓");

  // A2 허용 — yes ✓ + 확인권고 + http 링크.
  await items.nth(1).click();
  card = page.getByTestId("complex-card");
  await expect(card.getByTestId("pet-status")).toContainText("✓");
  await expect(card.getByTestId("pet-confirm-badge")).toBeVisible();
  await expect(card.getByTestId("pet-source-link")).toBeVisible();

  // A3 금지 — no ✗ + urn 비링크 + 확인권고.
  await items.nth(2).click();
  card = page.getByTestId("complex-card");
  await expect(card.getByTestId("pet-status")).toContainText("✗");
  await expect(card.getByTestId("pet-source-agent")).toBeVisible();
  await expect(card.getByTestId("pet-source-link")).toHaveCount(0);
  await expect(card.getByTestId("pet-confirm-badge")).toBeVisible();

  // A4 불명 — unknown △확인 불가 + 확인권고.
  await items.nth(3).click();
  card = page.getByTestId("complex-card");
  await expect(card.getByTestId("pet-status")).toContainText("확인 불가");
  await expect(card.getByTestId("pet-confirm-badge")).toBeVisible();

  // A5 미조사 — none "정보 없음" + 확인권고(미조사여도) + 출처 없음.
  await items.nth(4).click();
  card = page.getByTestId("complex-card");
  await expect(card.getByTestId("pet-status")).toHaveText("정보 없음");
  await expect(card.getByTestId("pet-confirm-badge")).toBeVisible();
  await expect(card.getByTestId("pet-source-link")).toHaveCount(0);
  await expect(card.getByTestId("pet-source-agent")).toHaveCount(0);

  // 데모 스크린샷: conditional + caveats + 확인권고 + gym 공존(A1).
  await items.nth(0).click();
  card = page.getByTestId("complex-card");
  await card.scrollIntoViewIfNeeded();
  await card.screenshot({ path: "test-results/pet-card.png" });

  expect(consoleErrors, consoleErrors.join("\n")).toEqual([]);
});
