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

  return (
    <main data-testid="app-root" className="flex h-screen flex-col md:flex-row">
      <aside className="flex w-full flex-col overflow-y-auto border-zinc-200 md:w-96 md:border-r">
        <h1 data-testid="app-title" className="px-4 pt-4 text-xl font-bold">
          ht-estate
        </h1>
        <FilterPanel onSearch={onSearch} />
        {error && (
          <p data-testid="search-error" className="px-4 text-sm text-red-600">
            {error}
          </p>
        )}
        <section data-testid="results" className="flex flex-col gap-1 px-4 pb-2">
          {candidates.map((candidate) => (
            <button
              key={candidate.complex_id}
              type="button"
              data-testid="result-item"
              onClick={() => onSelect(candidate)}
              className="rounded px-2 py-1 text-left text-sm hover:bg-zinc-100"
            >
              {candidate.name ?? candidate.complex_id}
            </button>
          ))}
        </section>
        {selected && (
          <div className="p-4">
            <ComplexCard candidate={selected} />
          </div>
        )}
      </aside>
      <div className="min-h-[20rem] flex-1">
        <MapView candidates={candidates} onBoundsChange={onBoundsChange} onSelect={onSelect} />
      </div>
    </main>
  );
}
