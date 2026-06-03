// 검색 API 클라이언트 — POST {API_BASE}/complexes/{search,markers}.

import type { Bbox, Candidate, HardFilterSpec, MarkerCandidate } from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

/** 랭크 리스트 + 상세용 — top-N 후보(criteria_eval 포함). */
export async function searchComplexes(
  spec: HardFilterSpec,
  bbox?: Bbox,
  signal?: AbortSignal,
): Promise<Candidate[]> {
  const body: HardFilterSpec = bbox ? { ...spec, ...bbox } : spec;
  const res = await fetch(`${API_BASE}/complexes/search`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok) throw new Error(`search failed: ${res.status}`);
  return (await res.json()) as Candidate[];
}

/** 지도 마커 피드 — 뷰포트 내 전체 단지(경량). 동일 hard 필터 존중, 랭킹·criteria_eval 없음. */
export async function fetchMarkers(
  spec: HardFilterSpec,
  bbox?: Bbox,
  signal?: AbortSignal,
): Promise<MarkerCandidate[]> {
  const body: HardFilterSpec = bbox ? { ...spec, ...bbox } : spec;
  const res = await fetch(`${API_BASE}/complexes/markers`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok) throw new Error(`markers failed: ${res.status}`);
  return (await res.json()) as MarkerCandidate[];
}
