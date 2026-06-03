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

// ── 평당가 tier (마커/클러스터 색) ──────────────────────────────────────────
const PYEONG = 3.3058; // 1평 = 3.3058㎡

/** 평당가(만원/평) — 대표 거래의 헤드라인 금액(매매=price, 전월세=deposit) / 전용평. 없으면 null. */
export function pricePerPyeong(c: Candidate): number | null {
  const rep = c.representative_trade;
  if (!rep || rep.net_area == null || rep.net_area <= 0) return null;
  const amount = rep.price ?? rep.deposit; // 매매 price 우선, 없으면 전월세 보증금
  if (amount == null) return null;
  return amount / (rep.net_area / PYEONG);
}

/** 뷰포트 결과 평당가의 분위수(20/40/60/80%) 경계 4개 → 적응적 5-tier. 비면 빈 배열. */
export function tierBoundaries(values: number[]): number[] {
  const xs = values.filter((v) => Number.isFinite(v)).sort((a, b) => a - b);
  if (xs.length < 2) return [];
  const q = (p: number) => xs[Math.min(xs.length - 1, Math.floor(p * xs.length))];
  return [q(0.2), q(0.4), q(0.6), q(0.8)];
}

/** 평당가 → tier 1..5(낮음→높음), 데이터 없으면 0(중립). boundaries 비면 3(중간). */
export function tierOf(ppp: number | null, boundaries: number[]): 0 | 1 | 2 | 3 | 4 | 5 {
  if (ppp == null) return 0;
  if (boundaries.length < 4) return 3;
  if (ppp < boundaries[0]) return 1;
  if (ppp < boundaries[1]) return 2;
  if (ppp < boundaries[2]) return 3;
  if (ppp < boundaries[3]) return 4;
  return 5;
}

/** tier → CSS 색 변수. 0=중립(ink2). */
export function tierColor(tier: number): string {
  return tier >= 1 && tier <= 5 ? `var(--t${tier})` : "var(--ink2)";
}

// ── 아웃링크 (크롤링 아님 — 새 탭으로 그쪽 검색을 연다) ──────────────────────
/** 네이버 부동산 모바일 검색 — 단지명 쿼리. */
export function naverSearchUrl(name: string | null): string {
  return `https://m.land.naver.com/search/result/${encodeURIComponent(name ?? "")}`;
}

/** 호갱노노 검색 — 단지명 쿼리. */
export function hogangnonoSearchUrl(name: string | null): string {
  return `https://hogangnono.com/search/${encodeURIComponent(name ?? "")}`;
}
