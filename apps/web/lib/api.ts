// hard filter 검색 API 클라이언트 — POST {API_BASE}/complexes/search.

import type { Bbox, Candidate, HardFilterSpec } from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

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
  if (!res.ok) {
    throw new Error(`search failed: ${res.status}`);
  }
  return (await res.json()) as Candidate[];
}
