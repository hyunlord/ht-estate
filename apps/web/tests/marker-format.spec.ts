import { expect, test } from "@playwright/test";

import { markerFeedLabel } from "../lib/markers";
import type { MarkerCandidate } from "../lib/types";

// 마커 라벨 순수 함수 단위 테스트 — 월세 보증금/월세 둘 다. (admin-clustering: cellSize geometric
// 셀은 제거 — 클러스터링은 서버 행정 계층, 건물 레벨선 서버 개별 마커 직접 렌더.)

function mk(p: Partial<MarkerCandidate>): MarkerCandidate {
  return { complex_id: "x", name: "x", lat: 37.5, lng: 127, price: null, net_area: 84, ...p };
}

test.describe("② markerFeedLabel — 월세 보증금/월세 둘 다", () => {
  test("월세: 보증금(억)/월세", () => {
    expect(markerFeedLabel(mk({ rent_type: "monthly", deposit: 30000, monthly_rent: 100 })))
      .toBe("3억/100");
  });
  test("월세: 보증금<1억은 만 단위/월세 (리스트 일관 '1,500만/100')", () => {
    expect(markerFeedLabel(mk({ rent_type: "monthly", deposit: 1500, monthly_rent: 100 })))
      .toBe("1,500만/100");
  });
  test("전세: 보증금(억)", () => {
    expect(markerFeedLabel(mk({ rent_type: "jeonse", deposit: 90000 }))).toBe("9억");
  });
  test("매매: price(억)", () => {
    expect(markerFeedLabel(mk({ price: 142000 }))).toBe("14.2억");
  });
  test("데이터 없음: null", () => {
    expect(markerFeedLabel(mk({ price: null }))).toBeNull();
  });
});
