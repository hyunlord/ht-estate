import { expect, test } from "./_criteria";

// 온디맨드 디테일뷰(ux-1) — API mock(키리스). 상세 진입 시 gym/pet pending 스피너 → 폴링 → 채움.
// pet은 확인 배지+caveats 전수. unavailable이어도 검색 캐시 fallback 유지(무한 스피너 없음).

const base = {
  approval_date: "2020-01-01", parking_ratio: 1.5, parking_underground: 100, household_count: 300,
  lat: 37.5, lng: 127.04, source_url: "https://k-apt.example/x", transaction_count: 0,
  price_min: null, price_max: null, representative_trade: null,
};

// 검색 캐시에는 gym/pet 미조사(none) — 상세 온디맨드가 채운다.
const CANDIDATES = [
  {
    ...base, complex_id: "A1", name: "역삼자이오피스텔",
    gym: { has_gym: "none", confidence: null, evidence: null, sources: [] },
    pet: { pet_allowed: "none", confidence: null, evidence: null, caveats: [],
      confirm_with_office: true, sources: [] },
  },
];

const READY = {
  complex_id: "A1",
  gym: {
    status: "ready",
    summary: { has_gym: "yes", confidence: 0.9, evidence: "단지 내 피트니스(블로그)",
      sources: [{ source_type: "blog", source_url: "https://blog.example/g" }] },
  },
  pet: {
    status: "ready",
    summary: { pet_allowed: "conditional", confidence: 0.7, evidence: "관리규약 — 소형견 허용",
      caveats: ["소형견만", "마릿수 제한"], confirm_with_office: true,
      sources: [{ source_type: "cafe", source_url: "https://cafe.example/p" }] },
  },
};

test("detail on-demand: pending spinner → fill with provenance, pet badge+caveats", async ({
  page,
}) => {
  await page.route("**/complexes/search", (route) => route.fulfill({ json: CANDIDATES }));
  await page.route("**/complexes/markers", (route) => route.fulfill({ json: { mode: "markers", markers: [], clusters: [] } }));
  let calls = 0;
  await page.route("**/enrichment", (route) => {
    calls += 1;
    if (calls === 1) {
      route.fulfill({
        json: {
          complex_id: "A1",
          gym: { status: "pending", summary: null },
          pet: { status: "pending", summary: null },
        },
      });
    } else {
      route.fulfill({ json: READY });
    }
  });

  await page.goto("/", { waitUntil: "networkidle" }); // 마운트 auto-search가 리스트 채움
  const items = page.getByTestId("result-item");
  await expect(items).toHaveCount(1);
  await items.nth(0).click();

  const card = page.getByTestId("complex-card");
  // 1차 폴링 pending → 스피너 노출(확인 중)
  await expect(card.getByTestId("gym-pending")).toBeVisible();

  // 2차 폴링 ready → 값 + 출처 딥링크 채움
  await expect(card.getByTestId("gym-status")).toHaveText("✓");
  await expect(card.getByTestId("gym-evidence")).toContainText("피트니스");
  await expect(card.getByTestId("gym-source-link")).toHaveAttribute(
    "href",
    "https://blog.example/g",
  );

  // pet — 조건부 + caveats + 확인 배지(advisory 정직)
  await expect(card.getByTestId("pet-status")).toContainText("조건부");
  await expect(card.getByTestId("pet-caveats")).toContainText("소형견만");
  await expect(card.getByTestId("pet-confirm-badge")).toBeVisible();
});
