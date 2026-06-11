import { expect, test } from "./_criteria";

// instant-perf: 분리 렌더 — 맵(/markers)이 리스트(/search)를 기다리지 않는다. /markers를 즉시,
// /search를 지연시키면: 맵 로딩은 곧 사라지고(마커 paint) 리스트는 아직 로딩 → 둘이 독립.
// (단일 loading이던 구버전이면 맵 로딩이 max(둘)까지 유지돼 이 테스트가 실패 — 회귀 가드.)

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

const MARKERS = {
  mode: "markers",
  markers: Array.from({ length: 8 }, (_, i) => ({
    complex_id: `M${i}`, name: `마커${i}`, lat: 37.49 + i * 0.001, lng: 127.03, price: 90000, net_area: 60,
  })),
  clusters: [],
};

test("맵(/markers)이 리스트(/search) 안 기다리고 먼저 렌더(분리 로딩)", async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));
  page.on("pageerror", (e) => consoleErrors.push(e.message));

  // /markers 즉시 · /search 지연(800ms) → 맵이 먼저 끝나야 함
  await page.route("**/complexes/markers", (route) => route.fulfill({ json: MARKERS }));
  await page.route("**/complexes/search", async (route) => {
    await new Promise((r) => setTimeout(r, 800));
    await route.fulfill({ json: FIVE });
  });

  await page.goto("/", { waitUntil: "commit" });

  // 맵 로딩은 /markers 끝나면 곧 사라진다(/search 800ms 안 기다림).
  await expect(page.getByTestId("map-loading")).toBeHidden({ timeout: 3000 });
  // 그 시점 리스트는 아직 비어있다(/search 미완) — 분리 렌더 증거.
  await expect(page.getByTestId("result-item")).toHaveCount(0);
  // 잠시 후 /search 도착 → 리스트 채워짐.
  await expect(page.getByTestId("result-item")).toHaveCount(5);

  expect(consoleErrors, consoleErrors.join("\n")).toEqual([]);
});
