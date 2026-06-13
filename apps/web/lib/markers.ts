// 마커 라벨(월세 보증금/월세). 순수·테스트 가능.
// admin-clustering: 클라 geometric 클러스터링(cellSize/clusterMarkers grid 병합)은 제거 — 클러스터링은
// 서버 행정 계층(시도→시군구→읍면동)이 담당하고, 건물 레벨선 서버 개별 마커를 직접 렌더(grid 병합 0).

import { wonToShort } from "./format";
import type { MarkerCandidate } from "./types";

// 마커 라벨 — 현 deal_type 대표거래 기준(리스트/디테일과 일관):
//   월세 = "보증금/월세"(예 "1,500만/100"·"3억/100") · 전세 = 보증금(억) · 매매 = price(억/만). 없으면 null.
export function markerFeedLabel(m: MarkerCandidate): string | null {
  if (m.rent_type === "monthly" && m.deposit != null) {
    return `${wonToShort(m.deposit)}/${(m.monthly_rent ?? 0).toLocaleString()}`;
  }
  if (m.rent_type === "jeonse" && m.deposit != null) {
    return wonToShort(m.deposit);
  }
  if (m.price != null) {
    return wonToShort(m.price);
  }
  return null;
}
