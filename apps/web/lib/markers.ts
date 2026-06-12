// marker-zoom-rent: 마커 클러스터 입자도(줌-aware 건물스케일) + 라벨(월세 보증금/월세). 순수·테스트 가능.

import { wonToShort } from "./format";
import type { MarkerCandidate } from "./types";

// ① 줌-aware 셀 크기(°) — 깊은 줌(낮은 Kakao level)일수록 작은 셀(≈건물스케일) → 겹치는 건물만
// 병합·단일셀은 개별 price 마커. 구 0.0025*2^(level-3)(level4≈440m·"33단지")보다 훨씬 잘게.
// level2≈27m · 3≈53m · 4≈106m · 5≈210m · 6≈420m (위경도 1°≈88km@37.5°).
export function cellSize(level: number): number {
  return 0.0003 * Math.pow(2, Math.max(0, level - 2));
}

export interface Cell {
  lat: number;
  lng: number;
  members: MarkerCandidate[];
}

// 마커를 줌-aware 격자 셀로 묶기 — 셀당 중심(평균)+멤버. 단일멤버 셀은 호출부서 개별 마커로 렌더.
export function clusterMarkers(markers: MarkerCandidate[], level: number): Cell[] {
  const size = cellSize(level);
  const cells = new Map<string, MarkerCandidate[]>();
  for (const m of markers) {
    if (m.lat == null || m.lng == null) continue;
    const key = `${Math.floor(m.lat / size)}:${Math.floor(m.lng / size)}`;
    const bucket = cells.get(key);
    if (bucket) bucket.push(m);
    else cells.set(key, [m]);
  }
  return Array.from(cells.values()).map((members) => ({
    lat: members.reduce((s, m) => s + (m.lat ?? 0), 0) / members.length,
    lng: members.reduce((s, m) => s + (m.lng ?? 0), 0) / members.length,
    members,
  }));
}

// ② 마커 라벨 — 현 deal_type 대표거래 기준(리스트/디테일과 일관):
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
