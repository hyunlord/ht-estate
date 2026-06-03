// 가격 포맷 — 만원 단위 → 한국식 축약(지도 마커·카드 공용). 결정론(테스트 가능).

import type { Candidate } from "./types";

/** 만원 → "14.2억" / "9,000만" 축약. 1억 이상이면 억 단위(불필요한 .0 제거). */
export function wonToShort(manwon: number): string {
  if (manwon >= 10000) {
    const eok = manwon / 10000;
    const s = eok.toFixed(1).replace(/\.0$/, "");
    return `${s}억`;
  }
  return `${manwon.toLocaleString()}만`;
}

/** 후보의 지도 마커 라벨 — 대표 실거래 금액(거래유형별). 없으면 null(마커는 단지명만). */
export function markerLabel(c: Candidate): string | null {
  const rep = c.representative_trade;
  if (rep) {
    if (rep.rent_type === "monthly" && rep.deposit != null) {
      return `${wonToShort(rep.deposit)}/${(rep.monthly_rent ?? 0).toLocaleString()}`;
    }
    if (rep.rent_type === "jeonse" && rep.deposit != null) {
      return wonToShort(rep.deposit);
    }
    if (rep.price != null) {
      return wonToShort(rep.price);
    }
  }
  if (c.price_min != null) return wonToShort(c.price_min);
  return null;
}
