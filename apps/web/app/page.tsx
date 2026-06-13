"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ChatPanel, type ChatThreadItem } from "@/components/ChatPanel";
import { DetailPanel } from "@/components/DetailPanel";
import { DetectedChips } from "@/components/DetectedChips";
import { MapView } from "@/components/MapView";
import { ResultList } from "@/components/ResultList";
import { TopBar } from "@/components/TopBar";
import { chat, fetchCriteria, fetchMarkers, searchComplexes } from "@/lib/api";
import { buildSpecFromChips, type ChipLevel } from "@/lib/nlChips";
import type {
  AreaUnit,
  Bbox,
  Candidate,
  CatalogCriterion,
  Detected,
  HardFilterSpec,
  MarkerCandidate,
  MarkerFeed,
  QuickFilter,
} from "@/lib/types";

// 최초 로드 즉시 1회 조회용 기본 bbox(서울 중심) — spec §5.1.
const DEFAULT_BBOX: Bbox = { min_lat: 37.4, max_lat: 37.7, min_lng: 126.8, max_lng: 127.2 };

// E5-2: complex_id에서 표시명 파싱 — 비아파트 id("of:11680:청담동:5:경원오피스텔")는 끝 세그먼트가
// 단지명. 아파트 opaque id(A1234…)는 id 그대로(폴백). 채팅 참조 칩 라벨/최소 candidate용.
function idName(id: string): string {
  const parts = id.split(":");
  return parts.length >= 5 ? parts[parts.length - 1] : id;
}

// 후보/마커에 없는 채팅 참조 id → 최소 MarkerCandidate(좌표 없음) → DetailPanel이 자체 enrich 조회.
function idToMarker(id: string): MarkerCandidate {
  return {
    complex_id: id, name: idName(id), lat: null, lng: null, price: null, net_area: null,
    deposit: null, monthly_rent: null, rent_type: null,
  };
}

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
  // 지도 피드(server-marker-clustering) — mode=markers(개별) 또는 clusters(grid 집계).
  const [feed, setFeed] = useState<MarkerFeed>({ mode: "markers", markers: [], clusters: [] });
  const [selected, setSelected] = useState<Candidate | null>(null);
  const [error, setError] = useState<string | null>(null);
  // instant-perf: 로딩 분리 — 맵(/markers)·리스트(/search)가 독립 렌더(먼저 보는 맵이 빠른 쪽으로 paint).
  const [mapLoading, setMapLoading] = useState(false);
  const [listLoading, setListLoading] = useState(false);
  const [unit, setUnit] = useState<AreaUnit>("pyeong");

  // #3b NL 검색 — 감지칩 + 칩별 강/약/제외 가중치. baseSpec(=NL 확정 spec)에서 칩 조정 spec 재구성.
  const [detected, setDetected] = useState<Detected[]>([]);
  const [unsupported, setUnsupported] = useState<string[]>([]);
  const [chipLevels, setChipLevels] = useState<Record<string, ChipLevel>>({});
  // reputation-routing: NL 주관 평판 의도 → detail 평판 섹션 pre-seed(자동 트리거)·감지 칩 표시.
  const [reputationQuery, setReputationQuery] = useState<string | null>(null);

  // frontend-polish-1: 조건 카탈로그(GET /criteria) — TopBar 퀵 토글 + ResultList 뱃지 값 포맷.
  // registry-driven(하드코딩 0). 실패는 graceful 빈([])(필터 칩만 빠짐·NL/검색 무영향·콘솔 무오염).
  const [quickFilters, setQuickFilters] = useState<QuickFilter[]>([]);
  const [criteriaCatalog, setCriteriaCatalog] = useState<CatalogCriterion[]>([]);

  // E5-2: 대화형 에이전트 스레드(멀티턴) + 단지 참조 클릭 시 지도 이동(panTo·nonce).
  const [thread, setThread] = useState<ChatThreadItem[]>([]);
  const [chatBusy, setChatBusy] = useState(false);
  const [panTo, setPanTo] = useState<{ lat: number; lng: number; nonce: number } | null>(null);
  const threadRef = useRef<ChatThreadItem[]>([]);
  const panNonce = useRef(0);

  const specRef = useRef<HardFilterSpec>({ limit: 100 });
  const bboxRef = useRef<Bbox>(DEFAULT_BBOX);
  const levelRef = useRef<number>(5); // 지도 줌(MapView 초기 level=5) — 마커 클러스터 행정단위 선택
  const feedRef = useRef<MarkerFeed>({ mode: "markers", markers: [], clusters: [] });
  const candidatesRef = useRef<Candidate[]>([]);
  const abortRef = useRef<AbortController | null>(null);
  const nlBaseRef = useRef<HardFilterSpec | null>(null); // NL 확정 spec(칩 재구성 기준)
  const detectedRef = useRef<Detected[]>([]); // 핸들러가 최신 감지 목록 참조용
  const chipLevelsRef = useRef<Record<string, ChipLevel>>({}); // 칩 레벨 최신값(연속 클릭 합성)

  const selectedRef = useRef<Candidate | null>(null); // detail-panel-polish ②: reconcile용 최신 선택

  // 렌더 중 ref 쓰기는 react-hooks 위반 → effect에서 동기(클릭 핸들러가 최신 목록 조회용).
  useEffect(() => {
    feedRef.current = feed;
    candidatesRef.current = candidates;
    detectedRef.current = detected;
    chipLevelsRef.current = chipLevels;
    selectedRef.current = selected;
    threadRef.current = thread;
  });

  // auto-viewport: (mount | 필터변경 | 지도 idle) → 리스트(/search) + 마커(/markers) 병렬 조회.
  // instant-perf: **분리 렌더** — 둘은 병렬이되 각자 끝나는 즉시 렌더(단일 loading로 max(둘) 블록 안 함).
  // 맵(먼저 보는 화면)은 /markers 끝나면 바로 paint(/search 안 기다림)·리스트는 /search 끝나면 채움.
  const runSearch = useCallback((spec: HardFilterSpec, bbox: Bbox) => {
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setMapLoading(true);
    setListLoading(true);
    fetchMarkers(spec, bbox, levelRef.current, ctrl.signal)
      .then((feed) => {
        if (ctrl.signal.aborted) return;
        setFeed(feed);
        setError(null);
      })
      .catch((e) => {
        if ((e as Error)?.name !== "AbortError") setError("검색 실패 — API 서버를 확인하세요.");
      })
      .finally(() => {
        if (abortRef.current === ctrl) setMapLoading(false);
      });
    searchComplexes(spec, bbox, ctrl.signal)
      .then((list) => {
        if (ctrl.signal.aborted) return;
        setCandidates(list);
        // detail-panel-polish ②: 딜타입 변경 등으로 재검색되면, 열린 패널을 새 결과의 같은 단지로
        // reconcile → 새 deal_type 대표거래/area_buckets로 즉시 갱신(중복 검색 없이 이 검색 재사용).
        // 새 결과에 없으면(필터 탈락) 옛 데이터 유지(닫지 않음 — 사용자가 명시 선택).
        const cur = selectedRef.current;
        if (cur) {
          const updated = list.find((c) => c.complex_id === cur.complex_id);
          if (updated) setSelected(updated);
        }
        setError(null);
      })
      .catch((e) => {
        if ((e as Error)?.name !== "AbortError") setError("검색 실패 — API 서버를 확인하세요.");
      })
      .finally(() => {
        if (abortRef.current === ctrl) setListLoading(false);
      });
  }, []);

  useEffect(() => {
    void runSearch(specRef.current, bboxRef.current);
  }, [runSearch]);

  // 조건 카탈로그 1회 로드 — registry-driven 필터 UI. 실패는 graceful(빈 칩·검색/NL 무영향).
  useEffect(() => {
    const ctrl = new AbortController();
    fetchCriteria(ctrl.signal)
      .then((cat) => {
        setQuickFilters(cat.quick_filters);
        setCriteriaCatalog(cat.criteria);
      })
      .catch(() => {
        /* graceful: /criteria 실패 → 퀵 토글만 비고 NL/검색은 정상 */
      });
    return () => ctrl.abort();
  }, []);

  // NL 감지칩 상태 초기화(수동 필터 전환·지우기 공용). 활성 칩이 없으면 no-op.
  const clearNl = useCallback(() => {
    if (nlBaseRef.current === null && detectedRef.current.length === 0) return;
    nlBaseRef.current = null;
    chipLevelsRef.current = {};
    setDetected([]);
    setUnsupported([]);
    setChipLevels({});
    setReputationQuery(null);
  }, []);

  const onFilterChange = useCallback(
    (spec: HardFilterSpec) => {
      specRef.current = { ...spec };
      clearNl(); // 수동 필터로 전환 → NL 감지칩은 stale이라 초기화(이중 입력: 둘 다 검색 구동)
      void runSearch(specRef.current, bboxRef.current);
    },
    [runSearch, clearNl],
  );

  const onBoundsChange = useCallback(
    (bbox: Bbox, level: number) => {
      bboxRef.current = bbox;
      levelRef.current = level; // 줌 갱신 → 다음 마커 조회의 클러스터 행정단위(구/동) 결정
      void runSearch(specRef.current, bbox);
    },
    [runSearch],
  );

  // E5-2: NL 바 제출 → 대화형 에이전트(/chat). 원샷 /search/nl 대체(주 NL 인터랙션).
  // 유저 턴 append → POST /chat { message, history(누적 스레드·백엔드 무상태), context(현 spec+bbox) }
  // → 에이전트 턴 append. updated_spec 있으면(필터 메시지) 기존 spec→search→map 경로로 적용(칩/지도
  // 갱신)·없으면(후속/열린질문) 필터/지도 불변. graceful: /chat 실패 시 인라인 에러 턴(앱 유지·crash 0).
  const onChatSend = useCallback(
    async (message: string) => {
      // history = 현재까지 누적 스레드(이번 user 메시지 제외) → role 매핑(agent→assistant).
      const history = threadRef.current
        .filter((it) => !it.error)
        .map((it) => ({
          role: (it.role === "agent" ? "assistant" : "user") as "assistant" | "user",
          content: it.content,
        }));
      setThread((t) => [...t, { role: "user", content: message }]);
      setChatBusy(true);
      const context: HardFilterSpec = { ...specRef.current, ...bboxRef.current };
      try {
        const r = await chat(message, history, context);
        setThread((t) => [
          ...t,
          { role: "agent", content: r.answer, citations: r.citations, referenced: r.referenced_complexes },
        ]);
        if (r.updated_spec) {
          // 메시지가 필터 변경 → 기존 search 경로로 적용(칩/지도/후보 갱신). 후속/열린질문(null)은 불변.
          specRef.current = { ...r.updated_spec };
          clearNl(); // 원샷 감지칩은 stale → 정리(활성 spec은 specRef가 보유·map/list 반영)
          runSearch(specRef.current, bboxRef.current);
        }
      } catch {
        setThread((t) => [
          ...t,
          { role: "agent", content: "응답을 가져오지 못했어요. 잠시 후 다시 시도해 주세요.", error: true },
        ]);
      } finally {
        setChatBusy(false);
      }
    },
    [runSearch, clearNl],
  );

  // 칩 강/약/제외 조정 → baseSpec에서 조정 spec 재구성 → 재검색(demote-not-exclude).
  // ref로 next를 계산해 setState 업데이터를 순수하게 유지(StrictMode 이중호출 안전).
  const onChipLevelChange = useCallback(
    (id: string, level: ChipLevel) => {
      const next = { ...chipLevelsRef.current, [id]: level };
      chipLevelsRef.current = next;
      setChipLevels(next);
      const base = nlBaseRef.current;
      if (base) {
        const spec = buildSpecFromChips(base, detectedRef.current, next);
        specRef.current = spec;
        void runSearch(spec, bboxRef.current);
      }
    },
    [runSearch],
  );

  // 감지칩 전체 지우기 → 빈 spec로 복귀.
  const onNlClear = useCallback(() => {
    clearNl();
    specRef.current = { limit: 100 };
    void runSearch(specRef.current, bboxRef.current);
  }, [runSearch, clearNl]);

  const onSelect = useCallback((c: Candidate) => setSelected(c), []);
  const onSelectId = useCallback((id: string) => {
    const full = candidatesRef.current.find((c) => c.complex_id === id);
    if (full) {
      setSelected(full);
      return;
    }
    const m = feedRef.current.markers.find((x) => x.complex_id === id);
    if (m) setSelected(markerToCandidate(m));
  }, []);

  // E5-2: 채팅 단지 참조 클릭 → 기존 select 경로 재사용(setSelected→DetailPanel) + 지도 그 단지로 이동.
  // 후보/마커서 lookup(좌표면 panTo). 둘 다 없으면(열린 답변 id) 최소 candidate로 detail 오픈(자체 enrich
  // fetch·좌표 없어 center skip). referenced_complexes는 백엔드가 실 id만 보장.
  const onRefClick = useCallback((id: string) => {
    const pan = (lat: number | null, lng: number | null) => {
      if (lat == null || lng == null) return;
      panNonce.current += 1;
      setPanTo({ lat, lng, nonce: panNonce.current });
    };
    const cand = candidatesRef.current.find((c) => c.complex_id === id);
    if (cand) {
      setSelected(cand);
      pan(cand.lat, cand.lng);
      return;
    }
    const m = feedRef.current.markers.find((x) => x.complex_id === id);
    if (m) {
      setSelected(markerToCandidate(m));
      pan(m.lat, m.lng);
      return;
    }
    setSelected(markerToCandidate(idToMarker(id)));  // detail-fetch 폴백(DetailPanel이 enrich 조회)
  }, []);

  const refLabel = useCallback((id: string) => {
    const c = candidatesRef.current.find((x) => x.complex_id === id);
    return c?.name ?? idName(id);
  }, []);

  const selectedId = selected?.complex_id ?? null;

  return (
    <div className="app" data-testid="app-root">
      <TopBar
        onChange={onFilterChange}
        onUnitChange={setUnit}
        onNlSearch={onChatSend}
        nlLoading={chatBusy}
        quickFilters={quickFilters}
      />
      <DetectedChips
        detected={detected}
        levels={chipLevels}
        unsupported={unsupported}
        reputationQuery={reputationQuery}
        onLevelChange={onChipLevelChange}
        onClear={onNlClear}
      />
      <div className="body">
        <ResultList
          candidates={candidates}
          selectedId={selectedId}
          loading={listLoading}
          unit={unit}
          onSelect={onSelect}
          catalog={criteriaCatalog}
        />
        <div className="map">
          <MapView
            feed={feed}
            selectedId={selectedId}
            loading={mapLoading}
            onBoundsChange={onBoundsChange}
            onSelectId={onSelectId}
            panTo={panTo}
          />
          {error && (
            <div className="loading" data-testid="search-error" style={{ color: "var(--miss)" }}>
              {error}
            </div>
          )}
          {/* E5-2: 채팅 스레드(옵션 3) — 지도 위 하단 도킹·필터 칩/지도와 공존. NL 바가 구동. */}
          <ChatPanel
            items={thread}
            busy={chatBusy}
            refLabel={refLabel}
            onRefClick={onRefClick}
            onClose={() => setThread([])}
          />
        </div>
        {/* detail-panel-sidebar: 우측 docked 컬럼(좌 ResultList 미러) — 맵을 덮지 않고 컨테인드.
            맵은 .map flex가 줄어들며 relayout(MapView ResizeObserver). */}
        {selected && (
          <DetailPanel
            key={`${selected.complex_id}-${selected.representative_trade?.rent_type ?? "sale"}`}
            candidate={selected}
            unit={unit}
            reputationQuery={reputationQuery}
            onClose={() => setSelected(null)}
          />
        )}
      </div>
    </div>
  );
}
