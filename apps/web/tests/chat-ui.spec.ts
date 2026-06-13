import { expect, test } from "./_criteria";

// E5-2 채팅 UI(옵션 3) — NL 바→멀티턴 스레드·단지 참조 클릭→지도+detail·updated_spec 적용·graceful.
// API mock(키리스·라이브 claude 無). /chat·/search·/markers mock. 단지 클릭은 기존 select→DetailPanel.

const base = {
  approval_date: "2019-01-01", parking_ratio: 1.2, parking_underground: 50, household_count: 200,
  lat: 37.5, lng: 127.04, source_url: null, transaction_count: 1, price_min: 50000, price_max: 50000,
  representative_trade: { net_area: 84, price: 50000, deposit: null, monthly_rent: null,
    rent_type: null, floor: 10, deal_date: "2026-05-01", match_confidence: 1.0 },
};
const CAND = [{ ...base, complex_id: "of:1", name: "강남오피스텔A" }];

async function send(page: import("@playwright/test").Page, text: string) {
  const input = page.getByTestId("nl-search");
  await input.fill(text);
  await input.press("Enter");
}

test("채팅: NL바→멀티턴 스레드·요청 바디(message+history+context)·답변/citations/ref·updated_spec 적용", async ({
  page,
}) => {
  const consoleErrors: string[] = [];
  page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));
  page.on("pageerror", (e) => consoleErrors.push(e.message));

  const chatBodies: Record<string, unknown>[] = [];
  let searchCalls = 0;
  await page.route("**/complexes/search", (r) => {
    searchCalls += 1;
    r.fulfill({ json: CAND });
  });
  await page.route("**/complexes/markers", (r) =>
    r.fulfill({ json: { mode: "markers", markers: [], clusters: [] } }));
  await page.route("**/enrichment", (r) =>
    r.fulfill({ json: { complex_id: "of:1", gym: { status: "unavailable", summary: null },
      pet: { status: "unavailable", summary: null } } }));
  await page.route("**/chat", (r) => {
    chatBodies.push(r.request().postDataJSON() as Record<string, unknown>);
    const turn = chatBodies.length;
    r.fulfill({
      json: turn === 1
        ? {
            answer: "강남오피스텔A를 추천합니다. 헬스장은 **미수집**입니다.",
            referenced_complexes: ["of:1"],
            citations: [{ source_type: "enrichment", source_url: "https://blog.naver.com/x/1" }],
            updated_spec: { property_type: "officetel", min_lat: 37.46, max_lat: 37.55,
              min_lng: 127.0, max_lng: 127.1, limit: 100 },
          }
        : { answer: "그 중 of:1이 주차가 낫습니다.", referenced_complexes: ["of:1"],
            citations: [], updated_spec: null },
    });
  });

  await page.goto("/", { waitUntil: "networkidle" });
  const searchAfterMount = searchCalls;

  // ── 1턴: 필터 메시지 ──
  await send(page, "강남 오피스텔 헬스장 있는");
  // 스레드에 유저+에이전트 턴
  await expect(page.getByTestId("chat-turn-user")).toHaveCount(1);
  const agent = page.getByTestId("chat-turn-agent");
  await expect(agent).toContainText("강남오피스텔A");
  await expect(agent).toContainText("미수집"); // 환각 0(정직)
  // 요청 바디: message + history(빈) + context(현 spec+bbox)
  expect((chatBodies[0] as { message?: string }).message).toBe("강남 오피스텔 헬스장 있는");
  expect((chatBodies[0] as { history?: unknown[] }).history).toEqual([]);
  expect((chatBodies[0] as { context?: object }).context).toBeTruthy();
  // citations 출처 링크
  await expect(page.getByTestId("chat-citation-link")).toHaveAttribute(
    "href", "https://blog.naver.com/x/1");
  // updated_spec → 기존 search 경로 재발화(필터/지도 갱신)
  await expect.poll(() => searchCalls).toBeGreaterThan(searchAfterMount);

  // ── 단지 참조 클릭 → 기존 select 경로(DetailPanel 오픈) ──
  await page.getByTestId("chat-ref").filter({ hasText: "강남오피스텔A" }).click();
  await expect(page.getByTestId("complex-card")).toBeVisible(); // setSelected→DetailPanel

  // ── 2턴(멀티턴): 후속 → history 누적(1턴 포함)·updated_spec=null(필터 불변) ──
  const beforeFollowup = searchCalls;
  await send(page, "그 중 주차 넉넉한 데는?");
  await expect(page.getByTestId("chat-turn-user")).toHaveCount(2);
  await expect.poll(() => chatBodies.length).toBe(2);
  const hist2 = (chatBodies[1] as { history?: { role: string; content: string }[] }).history ?? [];
  expect(hist2.length).toBe(2); // 1턴 유저+에이전트
  expect(hist2[0].role).toBe("user");
  expect(hist2[1].role).toBe("assistant");
  // updated_spec=null → search 추가 발화 없음(필터/지도 불변)
  await page.waitForTimeout(300);
  expect(searchCalls).toBe(beforeFollowup);

  // 옵션 3 공존: 채팅 패널 + 필터 칩 + 지도 동시 DOM
  await expect(page.getByTestId("chat-panel")).toBeVisible();
  await expect(page.getByTestId("infra-chips")).toBeVisible();
  await expect(page.getByTestId("map-container")).toBeVisible();

  expect(consoleErrors, consoleErrors.join("\n")).toEqual([]);
});

test("채팅 graceful: /chat 실패 → 인라인 에러 턴·앱 사용 가능(crash 0)", async ({ page }) => {
  await page.route("**/complexes/search", (r) => r.fulfill({ json: CAND }));
  await page.route("**/complexes/markers", (r) =>
    r.fulfill({ json: { mode: "markers", markers: [], clusters: [] } }));
  await page.route("**/chat", (r) => r.fulfill({ status: 500, json: { detail: "down" } }));

  await page.goto("/", { waitUntil: "networkidle" });
  await send(page, "강남 오피스텔");
  await expect(page.getByTestId("chat-turn-agent")).toContainText("가져오지 못했어요");
  // 앱 유지 — 지도/필터 작동
  await expect(page.getByTestId("map-container")).toBeVisible();
  await expect(page.getByTestId("infra-chips")).toBeVisible();
});
