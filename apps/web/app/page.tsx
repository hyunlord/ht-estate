"use client";

import { useCallback, useRef, useState } from "react";

import { ComplexCard } from "@/components/ComplexCard";
import { FilterPanel } from "@/components/FilterPanel";
import { MapView } from "@/components/MapView";
import { searchComplexes } from "@/lib/api";
import type { Bbox, Candidate, HardFilterSpec } from "@/lib/types";

export default function Home() {
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [selected, setSelected] = useState<Candidate | null>(null);
  const [error, setError] = useState<string | null>(null);
  const specRef = useRef<HardFilterSpec>({ limit: 50 });
  const bboxRef = useRef<Bbox | undefined>(undefined);

  const runSearch = useCallback(async (spec: HardFilterSpec, bbox?: Bbox) => {
    try {
      setCandidates(await searchComplexes(spec, bbox));
      setError(null);
    } catch {
      setError("검색 실패 — API 서버를 확인하세요.");
    }
  }, []);

  const onSearch = useCallback(
    (spec: HardFilterSpec) => {
      specRef.current = spec;
      void runSearch(spec, bboxRef.current);
    },
    [runSearch],
  );

  const onBoundsChange = useCallback(
    (bbox: Bbox) => {
      bboxRef.current = bbox;
      void runSearch(specRef.current, bbox);
    },
    [runSearch],
  );

  const onSelect = useCallback((candidate: Candidate) => setSelected(candidate), []);
  const selectedId = selected?.complex_id ?? null;

  return (
    <main data-testid="app-root" className="relative h-full w-full">
      {/* 지도 = 메인 캔버스(풀블리드). 필터·결과·상세는 그 위 오버레이. */}
      <div className="absolute inset-0 z-0">
        <MapView
          candidates={candidates}
          selectedId={selectedId}
          onBoundsChange={onBoundsChange}
          onSelect={onSelect}
        />
      </div>

      {/* 좌측 오버레이 — 브랜드 + 필터 + 랭크 리스트 */}
      <div className="pointer-events-none absolute inset-y-0 left-0 z-10 flex w-full flex-col gap-3 p-3 sm:w-[360px]">
        <header className="pointer-events-auto flex items-baseline gap-2 rounded-xl border border-border-soft bg-surface/85 px-4 py-3 backdrop-blur">
          <h1 data-testid="app-title" className="text-lg font-bold tracking-tight text-ink">
            ht-estate
          </h1>
          <span className="eyebrow">단지 탐색</span>
        </header>

        <div className="pointer-events-auto flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto rounded-xl border border-border-soft bg-surface/85 p-4 backdrop-blur">
          <FilterPanel onSearch={onSearch} />

          {error && (
            <p data-testid="search-error" className="font-mono text-xs text-pink">
              {error}
            </p>
          )}

          <section data-testid="results" className="flex flex-col gap-1 border-t border-border-soft pt-3">
            <div className="eyebrow mb-1 flex items-center justify-between">
              <span>결과 · 랭크순</span>
              <span className="text-faint">{candidates.length}</span>
            </div>
            {candidates.map((candidate) => {
              const on = candidate.complex_id === selectedId;
              return (
                <button
                  key={candidate.complex_id}
                  type="button"
                  data-testid="result-item"
                  onClick={() => onSelect(candidate)}
                  className={`truncate rounded-lg border px-3 py-2 text-left text-sm transition ${
                    on
                      ? "border-cool/50 bg-cool/10 text-ink"
                      : "border-transparent text-muted hover:border-border hover:bg-surface2"
                  }`}
                >
                  {candidate.name ?? candidate.complex_id}
                </button>
              );
            })}
          </section>
        </div>
      </div>

      {/* 우측 오버레이 — 선택 단지 상세(근거/출처) */}
      {selected && (
        <div className="pointer-events-none absolute inset-y-0 right-0 z-10 flex w-full flex-col p-3 sm:w-[380px]">
          <div className="pointer-events-auto min-h-0 overflow-y-auto">
            <ComplexCard candidate={selected} />
          </div>
        </div>
      )}
    </main>
  );
}
