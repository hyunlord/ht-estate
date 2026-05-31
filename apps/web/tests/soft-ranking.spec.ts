import { expect, test } from "@playwright/test";

// soft 랭킹 토글 — pet=필수 선택 시 요청 body에 soft가 실리고, 결과 순서가 반영되며
// 후보 수는 불변(demote-not-exclude). API mock이 soft에 따라 순서만 바꿔 반환(랭킹 계약 검증).

const base = {
  approval_date: "2020-01-01",
  parking_ratio: 1.5,
  parking_underground: 100,
  household_count: 300,
  lat: 37.5,
  lng: 127.04,
  source_url: null,
  transaction_count: 0,
  price_min: null,
  price_max: null,
  representative_trade: null,
  gym: null,
  pet: null,
};
const NEUTRAL = [
  { ...base, complex_id: "C1", name: "노펫단지" },
  { ...base, complex_id: "C2", name: "예스펫단지" },
];
const RANKED = [NEUTRAL[1], NEUTRAL[0]]; // pet required → 예스펫이 위

test("soft toggle sends pref and reorders results without changing count", async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));
  page.on("pageerror", (e) => consoleErrors.push(e.message));

  const softSeen: (string | null)[] = [];
  await page.route("**/complexes/search", async (route) => {
    const body = route.request().postDataJSON() as { soft?: { pet?: string } };
    const pet = body?.soft?.pet ?? null;
    softSeen.push(pet);
    await route.fulfill({ json: pet === "required" ? RANKED : NEUTRAL });
  });

  await page.goto("/", { waitUntil: "networkidle" });

  // 1) soft off(기본 none) → 중립 순서, soft 미전송.
  await page.getByTestId("search-button").click();
  await expect(page.getByTestId("result-item")).toHaveText(["노펫단지", "예스펫단지"]);

  // 2) pet=필수 → soft 전송 + 순서 반영(예스펫 위) + 후보 수 동일.
  await page.getByTestId("pet-pref").selectOption("required");
  await page.getByTestId("search-button").click();
  await expect(page.getByTestId("result-item")).toHaveText(["예스펫단지", "노펫단지"]);
  await expect(page.getByTestId("result-item")).toHaveCount(2); // 수 불변(demote-not-exclude)

  // 토글 off 요청엔 soft 없음(null), 필수 요청엔 'required' 전달됨.
  expect(softSeen).toEqual([null, "required"]);

  await page.screenshot({ path: "test-results/soft-ranking.png", fullPage: true });
  expect(consoleErrors, consoleErrors.join("\n")).toEqual([]);
});
