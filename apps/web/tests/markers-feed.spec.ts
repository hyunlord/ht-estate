import { expect, test } from "./_criteria";


// 마커 피드(P4-3a-2) — 지도 마커 = /complexes/markers(뷰포트 전체), 리스트 = /complexes/search(랭크 top-N).
// 둘은 분리된 별도 조회. 마커 피드는 필터 존중. (마커 픽셀 렌더는 JS 키 필요 → 사용자; 여긴 피드 와이어링.)

// 리스트(search) top-N = 5건.
const FIVE = Array.from({ length: 5 }, (_, i) => ({
  complex_id: `L${i}`, name: `랭크단지${i}`, approval_date: "2018-01-01", parking_ratio: 1.4,
  parking_underground: 100, household_count: 500, lat: 37.5, lng: 127.04, source_url: null,
  transaction_count: 1, price_min: 100000, price_max: 100000, gym: null, pet: null,
  representative_trade: {
    net_area: 84.97, price: 100000, deposit: null, monthly_rent: null, rent_type: null,
    floor: 5, deal_date: "2025-04-01", match_confidence: 1.0,
  },
  criteria_eval: [],
}));

// 마커 피드 = 뷰포트 전체(>100 — top-100 한계 너머). 최소 필드.
const MANY = Array.from({ length: 120 }, (_, i) => ({
  complex_id: `M${i}`, name: `마커단지${i}`,
  lat: 37.49 + i * 0.0002, lng: 127.03 + (i % 10) * 0.001,
  price: 90000 + i * 200, net_area: 59 + (i % 30),
}));

test("marker feed (/markers) is separate from rank list (/search) + respects filter", async ({
  page,
}) => {
  const consoleErrors: string[] = [];
  page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));
  page.on("pageerror", (e) => consoleErrors.push(e.message));

  let searchCount = 0;
  const markerBodies: Record<string, unknown>[] = [];
  await page.route("**/complexes/search", (route) => {
    searchCount += 1;
    route.fulfill({ json: FIVE });
  });
  await page.route("**/complexes/markers", (route) => {
    markerBodies.push(route.request().postDataJSON() as Record<string, unknown>);
    route.fulfill({ json: { mode: "markers", markers: MANY, clusters: [] } });
  });

  await page.goto("/", { waitUntil: "networkidle" });

  // 리스트 = search top-N(5). 마커 피드 = 별도 조회(120건 — >100, 마커≠리스트).
  await expect(page.getByTestId("result-item")).toHaveCount(5);
  expect(searchCount).toBeGreaterThan(0);
  expect(markerBodies.length).toBeGreaterThan(0); // 마커 피드가 마운트에 자동 발사됨(검색 버튼 없음)
  // 마커 피드는 bbox 바운드(뷰포트).
  expect(typeof (markerBodies[0] as Record<string, number>).min_lat).toBe("number");

  // 마커 피드도 필터 존중 — 메이저 칩(세대당주차) → 마커 요청 spec에 반영(filter-trim).
  await page.getByTestId("chip-parking_q").click();
  const mb = markerBodies.at(-1) as { parking_ratio_gte?: number };
  expect(mb.parking_ratio_gte).toBe(1.0);

  expect(consoleErrors, consoleErrors.join("\n")).toEqual([]);
});

// server-marker-clustering — 저줌/고밀도서 서버가 mode='clusters'(grid 집계) 반환. 프론트가 새 shape를
// crash 없이 처리(부천-굶김 픽스의 클라이언트 측). (지도 픽셀 렌더는 JS 키 필요 → 사람 — 여긴 피드 처리.)
test("marker feed clusters mode handled without crash", async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));
  page.on("pageerror", (e) => consoleErrors.push(e.message));

  await page.route("**/complexes/search", (route) => route.fulfill({ json: FIVE }));
  await page.route("**/complexes/markers", (route) =>
    route.fulfill({
      json: {
        mode: "clusters", markers: [],
        clusters: [
          { lat: 37.47, lng: 126.82, count: 1200, region: "부천오정구" }, // 부천 — 편향 제거+지역명
          { lat: 37.50, lng: 127.05, count: 800, region: "강남구" }, // 강남
        ],
      },
    }),
  );

  await page.goto("/", { waitUntil: "networkidle" });
  await expect(page.getByTestId("result-item")).toHaveCount(5); // 리스트 정상(별도 경로)
  expect(consoleErrors, consoleErrors.join("\n")).toEqual([]); // 새 shape crash 0
});
