import { expect, test } from "./_criteria";

// #3b NL 검색 — (1) 질의 → 감지칩 + unsupported 노트 (2) 칩 강/약/제외 → 조정 spec 재검색.
// 키리스: /search/nl·/search·/markers 전부 mock. spec↔칩 매핑은 재검색 요청 body로 검증.

const CAND = {
  complex_id: "C1", name: "역삼자이", approval_date: "2016-06-22", parking_ratio: 1.5,
  parking_underground: 615, household_count: 408, lat: 37.5, lng: 127.04,
  source_url: "https://k-apt.example/C1", transaction_count: 1, price_min: 142000, price_max: 142000,
  gym: null, pet: null,
  representative_trade: {
    net_area: 84.97, price: 142000, deposit: null, monthly_rent: null, rent_type: null,
    floor: 12, deal_date: "2025-04-15", match_confidence: 1.0,
  },
  criteria_eval: [],
};

// "강남 역세권 어린이집 신축, 강아지 되면 좋고" 파싱 결과(역세권·어린이집=hard / 신축·반려=soft).
const NL_RESPONSE = {
  spec: {
    deal_type: "sale",
    has_daycare: true,
    subway_walkable: true,
    soft: { gym: "none", pet: "preferred", criteria: [{ key: "approval_year", weight: 1 }] },
    limit: 50,
  },
  detected: [
    { criterion_key: "has_daycare", label: "어린이집", mode: "hard", phrase: "어린이집" },
    { criterion_key: "subway_time", label: "역세권(지하철 도보)", mode: "hard", phrase: "역세권" },
    { criterion_key: "approval_year", label: "신축 정도", mode: "soft", phrase: "신축" },
    { criterion_key: "pet", label: "반려동물", mode: "soft", phrase: "강아지 되면 좋고" },
  ],
  unsupported: ["수영장"],
  candidates: [CAND],
};

test("NL query → detected chips + unsupported, chip weight tuning re-searches (demote-not-exclude)", async ({
  page,
}) => {
  const consoleErrors: string[] = [];
  page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));
  page.on("pageerror", (e) => consoleErrors.push(e.message));

  const searchBodies: Record<string, unknown>[] = [];
  await page.route("**/complexes/search/nl", (route) =>
    route.fulfill({ json: NL_RESPONSE }),
  );
  await page.route("**/complexes/search", (route) => {
    searchBodies.push(JSON.parse(route.request().postData() ?? "{}"));
    return route.fulfill({ json: [CAND] });
  });
  await page.route("**/complexes/markers", (route) => route.fulfill({ json: { mode: "markers", markers: [], clusters: [] } }));

  await page.goto("/", { waitUntil: "networkidle" });

  // NL 질의 제출(Enter) → 감지칩 + unsupported.
  await page.getByTestId("nl-search").fill("강남 역세권 어린이집 신축, 강아지 되면 좋고");
  await page.getByTestId("nl-search").press("Enter");

  await expect(page.getByTestId("detected-chips")).toBeVisible();
  await expect(page.getByTestId("detected-chip-has_daycare")).toBeVisible();
  await expect(page.getByTestId("detected-chip-subway_time")).toBeVisible();
  await expect(page.getByTestId("detected-chip-approval_year")).toBeVisible();
  await expect(page.getByTestId("detected-chip-pet")).toBeVisible();
  await expect(page.getByTestId("nl-unsupported")).toContainText("수영장");

  // hard 감지(어린이집)는 초기 강, soft 감지(신축)는 약.
  await expect(page.getByTestId("chip-level-has_daycare-strong")).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await expect(page.getByTestId("chip-level-approval_year-soft")).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  // 결과가 채워졌다(랭크 후보).
  await expect(page.getByTestId("result-item")).toHaveText(/역삼자이/);

  // (A) 어린이집 강→약: hard 필드 clear + soft 강등. demote-not-exclude.
  let n = searchBodies.length;
  await page.getByTestId("chip-level-has_daycare-soft").click();
  await expect.poll(() => searchBodies.length).toBeGreaterThan(n);
  let last = searchBodies[searchBodies.length - 1];
  expect(last.has_daycare, "약 강등 시 hard 필드 제거").toBeUndefined();
  expect(
    (last.soft as { criteria?: { key: string; weight: number }[] }).criteria,
    "약 강등 시 soft 랭킹 추가",
  ).toContainEqual({ key: "has_daycare", weight: 1 });
  // 역세권 hard는 유지(강 그대로).
  expect(last.subway_walkable, "조정 안 한 hard는 유지").toBe(true);

  // (B) 역세권 강→제외: hard 필드 clear, soft에도 없음.
  n = searchBodies.length;
  await page.getByTestId("chip-level-subway_time-exclude").click();
  await expect.poll(() => searchBodies.length).toBeGreaterThan(n);
  last = searchBodies[searchBodies.length - 1];
  expect(last.subway_walkable, "제외 시 hard 필드 제거").toBeUndefined();
  const softB = last.soft as { criteria?: { key: string }[] };
  expect(softB.criteria?.some((c) => c.key === "subway_time"), "제외는 soft에도 없음").toBeFalsy();

  // (C) 신축(soft) 약→강: soft 가중치 2.0(여전히 soft — SET 불변).
  n = searchBodies.length;
  await page.getByTestId("chip-level-approval_year-strong").click();
  await expect.poll(() => searchBodies.length).toBeGreaterThan(n);
  last = searchBodies[searchBodies.length - 1];
  const softC = last.soft as { criteria?: { key: string; weight: number }[] };
  expect(softC.criteria).toContainEqual({ key: "approval_year", weight: 2 });
  expect(last.approval_year_min, "soft 강은 hard 승격 아님").toBeUndefined();

  expect(consoleErrors, consoleErrors.join("\n")).toEqual([]);
});
