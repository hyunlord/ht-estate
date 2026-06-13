import { expect, test } from "./_criteria";


// soft 랭킹 — 인프라 칩(어린이집)=soft 조건 → 요청 body soft.criteria 실림 + 순서 반영 + 후보 수 불변
// (demote-not-exclude). v2: gym/pet 셀렉트 폐기 → 칩이 일반화 soft criteria로 데모트-낫-익스클루드 계약 보존.
const base = {
  approval_date: "2020-01-01", parking_ratio: 1.5, parking_underground: 100, household_count: 300,
  lat: 37.5, lng: 127.04, source_url: null, transaction_count: 0, price_min: null, price_max: null,
  representative_trade: null, gym: null, pet: null,
};
const NEUTRAL = [
  { ...base, complex_id: "C1", name: "데이케어없음단지" },
  { ...base, complex_id: "C2", name: "어린이집단지" },
];
const RANKED = [NEUTRAL[1], NEUTRAL[0]]; // has_daycare soft → 어린이집단지가 위

test("infra chip sends soft criterion and reorders without changing count", async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));
  page.on("pageerror", (e) => consoleErrors.push(e.message));

  const softSeen: (string | null)[] = [];
  await page.route("**/complexes/search", async (route) => {
    const body = route.request().postDataJSON() as { soft?: { gym?: string } };
    const gym = body?.soft?.gym ?? null;
    softSeen.push(gym);
    const ranked = gym === "preferred";
    await route.fulfill({ json: ranked ? RANKED : NEUTRAL });
  });
  await page.route("**/complexes/markers", (route) => route.fulfill({ json: { mode: "markers", markers: [], clusters: [] } }));

  await page.goto("/", { waitUntil: "networkidle" }); // 마운트 = soft 없음 → 중립 순서

  const items = page.getByTestId("result-item");
  await expect(items).toHaveCount(2);
  await expect(items.nth(0)).toContainText("데이케어없음단지");

  // filter-trim: 메이저 soft 칩(헬스장) → soft.gym=preferred + 순서 반영 + 후보 수 동일(2).
  await page.getByTestId("chip-gym_q").click();
  await expect(items.nth(0)).toContainText("어린이집단지");
  await expect(items).toHaveCount(2); // 수 불변(demote-not-exclude)

  expect(softSeen).toEqual([null, "preferred"]);
  await page.screenshot({ path: "test-results/soft-ranking.png", fullPage: true });
  expect(consoleErrors, consoleErrors.join("\n")).toEqual([]);
});
