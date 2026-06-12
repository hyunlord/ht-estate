import { expect, test } from "@playwright/test";

import { cellSize, markerFeedLabel } from "../lib/markers";
import type { MarkerCandidate } from "../lib/types";

// marker-zoom-rent: 순수 함수 단위 테스트(페이지 불요) — ② 월세 라벨 보증금/월세 · ① 줌-aware 셀.

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

test.describe("① cellSize — 줌-aware 건물스케일", () => {
  test("깊은 줌일수록 작은 셀(단조 증가)", () => {
    expect(cellSize(2)).toBeLessThan(cellSize(4));
    expect(cellSize(4)).toBeLessThan(cellSize(6));
  });
  test("street줌(level 4)은 구 0.0044°(≈440m)보다 훨씬 잘게(건물스케일)", () => {
    // 구 cellSize(4)=0.0025*2=0.005° → 새 값은 그보다 작아야(건물별 마커 가능).
    expect(cellSize(4)).toBeLessThan(0.0025);
    expect(cellSize(4)).toBeGreaterThan(0); // 양수
  });
});
