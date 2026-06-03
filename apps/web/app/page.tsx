"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { DetailPanel } from "@/components/DetailPanel";
import { MapView } from "@/components/MapView";
import { ResultList } from "@/components/ResultList";
import { TopBar } from "@/components/TopBar";
import { fetchMarkers, searchComplexes } from "@/lib/api";
import type { AreaUnit, Bbox, Candidate, HardFilterSpec, MarkerCandidate } from "@/lib/types";

// 최초 로드 즉시 1회 조회용 기본 bbox(서울 중심) — spec §5.1.
const DEFAULT_BBOX: Bbox = { min_lat: 37.4, max_lat: 37.7, min_lng: 126.8, max_lng: 127.2 };

// 마커(뷰포트 전체) 클릭이 리스트 top-N 밖이면 최소 정보로 상세 표시.
function markerToCandidate(m: MarkerCandidate): Candidate {
  const hasTrade = m.price != null || m.net_area != null;
  return {
    complex_id: m.complex_id,
    name: m.name,
    approval_date: null,
    parking_ratio: null,
    parking_underground: null,
    household_count: null,
    lat: m.lat,
    lng: m.lng,
    source_url: null,
    transaction_count: 0,
    price_min: m.price,
    price_max: m.price,
    representative_trade: hasTrade
      ? {
          net_area: m.net_area,
          price: m.price,
          deposit: null,
          monthly_rent: null,
          rent_type: null,
          floor: null,
          deal_date: null,
          match_confidence: null,
        }
      : null,
  };
}

export default function Home() {
  const [candidates, setCandidates] = useState<Candidate[]>([]); // 리스트/상세(랭크 top-N)
  const [markers, setMarkers] = useState<MarkerCandidate[]>([]); // 지도(뷰포트 전체)
  const [selected, setSelected] = useState<Candidate | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [unit, setUnit] = useState<AreaUnit>("pyeong");

  const specRef = useRef<HardFilterSpec>({ limit: 100 });
  const bboxRef = useRef<Bbox>(DEFAULT_BBOX);
  const markersRef = useRef<MarkerCandidate[]>([]);
  const candidatesRef = useRef<Candidate[]>([]);
  const abortRef = useRef<AbortController | null>(null);

  // 렌더 중 ref 쓰기는 react-hooks 위반 → effect에서 동기(클릭 핸들러가 최신 목록 조회용).
  useEffect(() => {
    markersRef.current = markers;
    candidatesRef.current = candidates;
  });

  // auto-viewport: (mount | 필터변경 | 지도 idle) → 리스트(/search) + 마커(/markers) 동시 조회.
  const runSearch = useCallback(async (spec: HardFilterSpec, bbox: Bbox) => {
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setLoading(true);
    try {
      const [list, feed] = await Promise.all([
        searchComplexes(spec, bbox, ctrl.signal),
        fetchMarkers(spec, bbox, ctrl.signal),
      ]);
      setCandidates(list);
      setMarkers(feed);
      setError(null);
    } catch (e) {
      if ((e as Error)?.name === "AbortError") return;
      setError("검색 실패 — API 서버를 확인하세요.");
    } finally {
      if (abortRef.current === ctrl) setLoading(false);
    }
  }, []);

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
  const onSelectId = useCallback((id: string) => {
    const full = candidatesRef.current.find((c) => c.complex_id === id);
    if (full) {
      setSelected(full);
      return;
    }
    const m = markersRef.current.find((x) => x.complex_id === id);
    if (m) setSelected(markerToCandidate(m));
  }, []);

  const selectedId = selected?.complex_id ?? null;

  return (
    <div className="app" data-testid="app-root">
      <TopBar onChange={onFilterChange} onUnitChange={setUnit} />
      <div className="body">
        <ResultList
          candidates={candidates}
          selectedId={selectedId}
          loading={loading}
          unit={unit}
          onSelect={onSelect}
        />
        <div className="map">
          <MapView
            markers={markers}
            selectedId={selectedId}
            loading={loading}
            onBoundsChange={onBoundsChange}
            onSelectId={onSelectId}
          />
          {error && (
            <div className="loading" data-testid="search-error" style={{ color: "var(--miss)" }}>
              {error}
            </div>
          )}
          {selected && (
            <DetailPanel candidate={selected} unit={unit} onClose={() => setSelected(null)} />
          )}
        </div>
      </div>
    </div>
  );
}
