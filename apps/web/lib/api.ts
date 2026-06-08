// 검색 API 클라이언트 — POST {API_BASE}/complexes/{search,markers,search/nl}.

import type {
  Bbox,
  Candidate,
  EnrichmentResponse,
  HardFilterSpec,
  MarkerCandidate,
  NlSearchResponse,
} from "./types";

// 기본은 **같은-오리진 프록시**(`/api` → next.config rewrites → 127.0.0.1:8000). 공개 URL 1개·CORS 불요.
// 별도 오리진 직접호출이 필요하면 NEXT_PUBLIC_API_BASE_URL로 절대 URL 주입(빌드타임 인라인).
const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "/api";

/** 단지 상세 온디맨드 gym/pet (ux-1) — GET /complexes/{id}/enrichment.
 * cache-hit이면 ready(summary)·miss면 pending(백그라운드 추출). 카드가 폴링으로 완료분 픽업. */
export async function fetchEnrichment(
  complexId: string,
  signal?: AbortSignal,
): Promise<EnrichmentResponse> {
  const res = await fetch(`${API_BASE}/complexes/${encodeURIComponent(complexId)}/enrichment`, {
    signal,
  });
  if (!res.ok) throw new Error(`enrichment failed: ${res.status}`);
  return (await res.json()) as EnrichmentResponse;
}

/** 자연어 질의 → 레지스트리-grounded spec + 감지칩 + 매핑불가 + 랭크 후보(#3b).
 * 백엔드가 LLM 파싱·grounding을 수행(프론트는 무수정 호출). 파싱 불가는 422 → throw. */
export async function searchNl(query: string, signal?: AbortSignal): Promise<NlSearchResponse> {
  const res = await fetch(`${API_BASE}/complexes/search/nl`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query }),
    signal,
  });
  if (!res.ok) throw new Error(`nl search failed: ${res.status}`);
  return (await res.json()) as NlSearchResponse;
}

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
