import { test as base, expect } from "@playwright/test";
import type { Page } from "@playwright/test";

// 공유 /criteria fixture(frontend-polish-1) — TopBar가 마운트 시 GET /criteria로 퀵 토글을 빌드하므로
// 모든 e2e가 이 카탈로그를 자동 주입(키리스·결정론·콘솔오염 0). 실 REGISTRY shape 미러.
// 이 파일에서 test/expect를 import하면 page에 /criteria 라우트가 자동 등록된다(spec 자체 라우트와 공존).
export const CRITERIA_MOCK = {
  criteria: [
    { key: "elem_dist", label: "초등학교 거리", value_type: "numeric", direction: "lower_better",
      soft_able: true, hard_able: true, hard_fields: ["elem_max_dist_m"], values: [] },
    { key: "hospital", label: "병원", value_type: "numeric", direction: "lower_better",
      soft_able: true, hard_able: true, hard_fields: ["hospital_max_dist_m"], values: [] },
    { key: "conv", label: "편의점", value_type: "numeric", direction: "higher_better",
      soft_able: true, hard_able: true, hard_fields: ["conv_count_1km_min"], values: [] },
    { key: "has_daycare", label: "어린이집", value_type: "bool", direction: "higher_better",
      soft_able: true, hard_able: true, hard_fields: ["has_daycare"], values: [] },
  ],
  quick_filters: [
    { id: "subway_poi", label: "역세권 500m", apply: "hard",
      hard_field: "subway_max_dist_m", hard_value: 500, soft_key: null },
    { id: "elem_school", label: "초등 500m", apply: "hard",
      hard_field: "elem_max_dist_m", hard_value: 500, soft_key: null },
    { id: "mart_poi", label: "마트 1km", apply: "hard",
      hard_field: "mart_count_1km_min", hard_value: 1, soft_key: null },
    { id: "conv_poi", label: "편의점 1km", apply: "hard",
      hard_field: "conv_count_1km_min", hard_value: 1, soft_key: null },
    { id: "hospital_poi", label: "병원 1km", apply: "hard",
      hard_field: "hospital_max_dist_m", hard_value: 1000, soft_key: null },
    { id: "has_daycare", label: "어린이집", apply: "soft",
      hard_field: null, hard_value: null, soft_key: "has_daycare" },
    { id: "elevator", label: "엘베", apply: "soft",
      hard_field: null, hard_value: null, soft_key: "elevator_count" },
  ],
};

export async function routeCriteria(page: Page): Promise<void> {
  await page.route("**/criteria", (route) => route.fulfill({ json: CRITERIA_MOCK }));
}

// page 픽스처 확장 — 모든 테스트에 /criteria 자동 라우트(spec이 별도 라우트 등록해도 공존).
export const test = base.extend({
  page: async ({ page }, provide) => {
    await routeCriteria(page);
    await provide(page); // Playwright fixture에 page 주입(React Hook 아님 — use* 이름 회피)
  },
});

export { expect };
