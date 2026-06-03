"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { DetailPanel } from "@/components/DetailPanel";
import { MapView } from "@/components/MapView";
import { ResultList } from "@/components/ResultList";
import { TopBar } from "@/components/TopBar";
import { searchComplexes } from "@/lib/api";
import type { Bbox, Candidate, HardFilterSpec } from "@/lib/types";

// 최초 로드 즉시 1회 조회용 기본 bbox(서울 중심) — spec §5.1.
const DEFAULT_BBOX: Bbox = { min_lat: 37.4, max_lat: 37.7, min_lng: 126.8, max_lng: 127.2 };

export default function Home() {
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [selected, setSelected] = useState<Candidate | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const specRef = useRef<HardFilterSpec>({ limit: 100 });
  const bboxRef = useRef<Bbox>(DEFAULT_BBOX);
  const abortRef = useRef<AbortController | null>(null);

  // auto-viewport 핵심: (mount | 필터변경 | 지도 idle) → 현 spec+bbox로 자동 조회(검색 버튼 없음).
  const runSearch = useCallback(async (spec: HardFilterSpec, bbox: Bbox) => {
    abortRef.current?.abort(); // 최신 요청만 반영(과호출 방지)
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setLoading(true);
    try {
      const next = await searchComplexes(spec, bbox, ctrl.signal);
      setCandidates(next);
      setError(null);
    } catch (e) {
      if ((e as Error)?.name === "AbortError") return; // 취소는 무시
      setError("검색 실패 — API 서버를 확인하세요.");
    } finally {
      if (abortRef.current === ctrl) setLoading(false);
    }
  }, []);

  // 최초 로드 1회 — 기본 bbox로 즉시 조회(지도 없어도 리스트 채움).
  useEffect(() => {
    void runSearch(specRef.current, bboxRef.current);
  }, [runSearch]);

  const onFilterChange = useCallback(
    (spec: HardFilterSpec) => {
      specRef.current = { ...spec };
      void runSearch(specRef.current, bboxRef.current);
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

  const onSelect = useCallback((c: Candidate) => setSelected(c), []);
  const selectedId = selected?.complex_id ?? null;

  return (
    <div className="app" data-testid="app-root">
      <TopBar onChange={onFilterChange} />
      <div className="body">
        <ResultList
          candidates={candidates}
          selectedId={selectedId}
          loading={loading}
          onSelect={onSelect}
        />
        <div className="map">
          <MapView
            candidates={candidates}
            selectedId={selectedId}
            loading={loading}
            onBoundsChange={onBoundsChange}
            onSelect={onSelect}
          />
          {error && (
            <div className="loading" data-testid="search-error" style={{ color: "var(--miss)" }}>
              {error}
            </div>
          )}
          {selected && <DetailPanel candidate={selected} onClose={() => setSelected(null)} />}
        </div>
      </div>
    </div>
  );
}
