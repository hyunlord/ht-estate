// 가격·면적 포맷 — 만원/㎡ → 한국식 표기(마커·카드 공용). 결정론(테스트 가능).

import type { AreaUnit, Candidate } from "./types";

export const SQM_PER_PYEONG = 3.3058; // 1평 = 3.3058㎡

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

// ── 면적 단위 (평/㎡ 토글) ───────────────────────────────────────────────
/** ㎡ → 표기 문자열(단위 항상 명시). 평=소수1, ㎡=원값. null이면 "—". */
export function formatArea(sqm: number | null | undefined, unit: AreaUnit): string {
  if (sqm == null) return "—";
  if (unit === "pyeong") return `${(sqm / SQM_PER_PYEONG).toFixed(1)}평`;
  return `${sqm}㎡`;
}

/** 현 단위 입력값 → ㎡(spec 전송 canonical). */
export function toSqm(value: number, unit: AreaUnit): number {
  return unit === "pyeong" ? value * SQM_PER_PYEONG : value;
}

/** ㎡ ↔ 현 단위 값 변환(단위 토글 시 입력값 보존용). */
export function convertArea(value: number, from: AreaUnit, to: AreaUnit): number {
  if (from === to) return value;
  return to === "pyeong" ? value / SQM_PER_PYEONG : value * SQM_PER_PYEONG;
}

// ── 평당가 tier (마커/클러스터 색) ──────────────────────────────────────────
/** 평당가(만원/평) 원시 — 금액/전용평. 마커·후보 공용. 없으면 null. */
export function ppp(
  amount: number | null | undefined,
  netAreaSqm: number | null | undefined,
): number | null {
  if (amount == null || netAreaSqm == null || netAreaSqm <= 0) return null;
  return amount / (netAreaSqm / SQM_PER_PYEONG);
}

/** 후보 평당가 — 대표 거래 헤드라인 금액(매매=price, 전월세=deposit) / 전용평. */
export function pricePerPyeong(c: Candidate): number | null {
  const rep = c.representative_trade;
  return ppp(rep?.price ?? rep?.deposit ?? null, rep?.net_area ?? null);
}

/** 마커 금액 → 라벨(억/만). 없으면 null. */
export function markerLabelAmount(price: number | null): string | null {
  return price != null ? wonToShort(price) : null;
}

// region-clustering: tier 입자도 5→7(세부 그라데이션). 평당가 색을 더 잘게 구별(클러스터·마커 공용).
export const TIER_COUNT = 7;

/** 뷰포트 결과 평당가의 적응적 분위수 경계(k/N, k=1..N-1) → N-tier. 표본 부족이면 빈 배열. */
export function tierBoundaries(values: number[]): number[] {
  const xs = values.filter((v) => Number.isFinite(v)).sort((a, b) => a - b);
  if (xs.length < 2) return [];
  const q = (p: number) => xs[Math.min(xs.length - 1, Math.floor(p * xs.length))];
  return Array.from({ length: TIER_COUNT - 1 }, (_, i) => q((i + 1) / TIER_COUNT));
}

/** 평당가 → tier 1..N(낮음→높음), 데이터 없으면 0(중립). boundaries 부족이면 중앙 tier. */
export function tierOf(ppp: number | null, boundaries: number[]): number {
  if (ppp == null) return 0;
  if (boundaries.length < TIER_COUNT - 1) return Math.ceil(TIER_COUNT / 2);
  for (let i = 0; i < boundaries.length; i++) if (ppp < boundaries[i]) return i + 1;
  return TIER_COUNT;
}

/** tier → CSS 색 변수(--t1..--tN). 0/범위밖=중립(ink2). */
export function tierColor(tier: number): string {
  return tier >= 1 && tier <= TIER_COUNT ? `var(--t${tier})` : "var(--ink2)";
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
