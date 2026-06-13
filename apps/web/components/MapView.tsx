"use client";

import { useEffect, useRef, useState } from "react";

import { ppp, TIER_COUNT, tierBoundaries, tierColor, tierOf } from "@/lib/format";
import { markerFeedLabel } from "@/lib/markers";
import {
  KAKAO_JS_KEY,
  loadKakaoMaps,
  type KakaoCustomOverlay,
  type KakaoMap,
  type KakaoMaps,
} from "@/lib/kakao";
import type { Bbox, MarkerCandidate, MarkerFeed } from "@/lib/types";

const GANGNAM = { lat: 37.4979, lng: 127.0476 };
const DEBOUNCE_MS = 280; // spec §5.1 — idle 후 250~300ms 디바운스
// admin-clustering: 클라 geometric 클러스터링 제거 — 클러스터링은 서버 행정 계층(시도→시군구→읍면동)이
// 담당하고, 건물 레벨선 서버 개별 마커를 직접 렌더(grid 병합 0). 아래 옛 주석은 참조용(과거 거동).
// marker-zoom-rent ①(과거): cellSize/clusterMarkers는 lib/markers(줌-aware 건물스케일)로 이동. 개별 모드는
// 항상 격자 클러스터 — 단일셀=개별 price 마커, 겹치는 건물만 병합(MARKER_CAP 강제클러스터 제거).

export function MapView({
  feed,
  selectedId,
  loading,
  onBoundsChange,
  onSelectId,
  panTo,
}: {
  feed: MarkerFeed; // server-marker-clustering: mode=markers(개별) 또는 clusters(grid 집계)
  selectedId: string | null;
  loading: boolean;
  onBoundsChange: (bbox: Bbox, level: number) => void; // level=줌(클러스터 행정단위 구/동 선택)
  onSelectId: (complexId: string) => void;
  // E5-2: 채팅 단지 참조 클릭 → 그 단지 좌표로 지도 이동(setCenter 재사용). nonce로 동일 좌표 재클릭도 발화.
  panTo?: { lat: number; lng: number; nonce: number } | null;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<KakaoMap | null>(null);
  const mapsRef = useRef<KakaoMaps | null>(null);
  const overlaysRef = useRef<KakaoCustomOverlay[]>([]);
  const feedRef = useRef(feed);
  const selectedIdRef = useRef(selectedId);
  const boundsCb = useRef(onBoundsChange);
  const selectCb = useRef(onSelectId);

  const [ready, setReady] = useState(false);
  const [unavailable, setUnavailable] = useState(false);

  useEffect(() => {
    feedRef.current = feed;
    selectedIdRef.current = selectedId;
    boundsCb.current = onBoundsChange;
    selectCb.current = onSelectId;
  });

  function boundaries(list: MarkerCandidate[]): number[] {
    return tierBoundaries(
      list.map((m) => ppp(m.price, m.net_area)).filter((v): v is number => v != null),
    );
  }

  function priceOverlay(
    maps: KakaoMaps,
    map: KakaoMap,
    m: MarkerCandidate,
    selId: string | null,
    bnd: number[],
  ): KakaoCustomOverlay {
    const wrap = document.createElement("div");
    wrap.className = m.complex_id === selId ? "mk sel" : "mk";
    const body = document.createElement("div");
    body.className = "body";
    body.style.setProperty("--mk-bg", tierColor(tierOf(ppp(m.price, m.net_area), bnd)));
    body.textContent = markerFeedLabel(m) ?? m.name ?? m.complex_id; // ② 월세=보증금/월세
    wrap.appendChild(body);
    wrap.addEventListener("click", () => selectCb.current(m.complex_id));
    return new maps.CustomOverlay({
      position: new maps.LatLng(m.lat as number, m.lng as number),
      content: wrap,
      map,
      xAnchor: 0.5,
      yAnchor: 1,
      clickable: true,
      zIndex: m.complex_id === selId ? 9 : 5,
    });
  }

  // 서버 클러스터(행정구역 집계) 오버레이 — 지역명+카운트 서클(원 크기 ∝ 카운트·색 ∝ 평당가 tier)·
  // 클릭→줌인+recenter. bnd=클러스터 평당가 분위 경계(세부 tier 색).
  function serverCluster(
    maps: KakaoMaps,
    map: KakaoMap,
    cl: {
      lat: number; lng: number; count: number; region?: string | null;
      ppp?: number | null; zoom_to?: number;
    },
    level: number,
    bnd: number[],
  ): KakaoCustomOverlay {
    // 카운트로 원 반경 스케일(sqrt·캡) — 큰 병합이 시각적으로 지배(겹침 인상 완화).
    const size = Math.round(Math.min(86, 42 + Math.sqrt(cl.count) * 2));
    const el = document.createElement("div");
    el.className = "cluster srv";
    el.style.background = tierColor(tierOf(cl.ppp ?? null, bnd)); // 탈-파랑: 구역 평당가 색
    el.style.width = `${size}px`;
    el.style.height = `${size}px`;
    const region = cl.region ?? "";
    el.innerHTML =
      `${region ? `<span class="r">${region}</span>` : ""}` +
      `<span class="n">${cl.count.toLocaleString()}</span>`;
    el.addEventListener("click", () => {
      // admin-clustering: 클릭→다음 세부 행정 밴드로 결정적 줌인(서버가 cl.zoom_to 산정·단일 소스).
      // 시도→시군구→읍면동→건물. zoom_to 없으면(레거시) 상대 줌(level-3) 폴백.
      const target = cl.zoom_to ?? Math.max(1, level - 3);
      map.setLevel(Math.max(1, target));
      map.setCenter(new maps.LatLng(cl.lat, cl.lng)); // 그 구역 중심으로
    });
    return new maps.CustomOverlay({
      position: new maps.LatLng(cl.lat, cl.lng),
      content: el,
      map,
      xAnchor: 0.5,
      yAnchor: 0.5,
      clickable: true,
    });
  }

  function renderMarkers() {
    const maps = mapsRef.current;
    const map = mapRef.current;
    if (!maps || !map) return;
    overlaysRef.current.forEach((o) => o.setMap(null));
    const next: KakaoCustomOverlay[] = [];
    const level = map.getLevel();
    const fd = feedRef.current;
    const selId = selectedIdRef.current;

    // 서버 클러스터 모드(저줌/고밀도) — 무편향·완전 집계를 그대로 렌더(클라 클러스터 안 함).
    // 색: 클러스터 평당가의 적응적 분위 경계로 tier 색(구역별 평당가 세부 구별 — 탈-파랑).
    if (fd.mode === "clusters") {
      const cbnd = tierBoundaries(
        fd.clusters.map((c) => c.ppp).filter((v): v is number => v != null),
      );
      for (const cl of fd.clusters) next.push(serverCluster(maps, map, cl, level, cbnd));
      overlaysRef.current = next;
      return;
    }

    // 개별 모드(읍면동 이하 줌인) — admin-clustering: 클라 geometric grid 병합 제거. 서버가 보낸
    // 개별 마커(읍면동 이하·≤MAX 바운드·안전망은 서버 폴백)를 **그대로 건물별 price 마커**로 렌더.
    const list = fd.markers;
    const bnd = boundaries(list);
    for (const m of list) {
      next.push(priceOverlay(maps, map, m, selId, bnd));
    }
    overlaysRef.current = next;
  }

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    loadKakaoMaps(KAKAO_JS_KEY)
      .then((maps) => {
        if (cancelled || !containerRef.current) return;
        mapsRef.current = maps;
        const map = new maps.Map(containerRef.current, {
          center: new maps.LatLng(GANGNAM.lat, GANGNAM.lng),
          level: 5,
        });
        mapRef.current = map;
        setReady(true);
        maps.event.addListener(map, "idle", () => {
          renderMarkers();
          if (timer) clearTimeout(timer);
          timer = setTimeout(() => {
            const b = map.getBounds();
            const sw = b.getSouthWest();
            const ne = b.getNorthEast();
            boundsCb.current(
              {
                min_lat: sw.getLat(),
                max_lat: ne.getLat(),
                min_lng: sw.getLng(),
                max_lng: ne.getLng(),
              },
              map.getLevel(), // 현 줌 → 서버 클러스터 행정단위(구/동) 선택
            );
          }, DEBOUNCE_MS);
        });
      })
      .catch(() => {
        if (!cancelled) setUnavailable(true);
      });
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // detail-panel-sidebar: 패널 open/close/resize로 맵 컨테이너 폭이 바뀌면 Kakao relayout(타일/마커
  // 재적합·블랭크/깨짐 방지). 중심 보존(relayout이 시프트할 수 있어 전후 center 복원). ResizeObserver로
  // 모든 폭 변화(패널·윈도) 일괄 처리. idle 재조회는 사용자 팬/줌만 — 프로그램 relayout은 bounds 미발화.
  useEffect(() => {
    if (!ready) return;
    const el = containerRef.current;
    const map = mapRef.current;
    if (!el || !map || typeof ResizeObserver === "undefined") return;
    let raf = 0;
    const ro = new ResizeObserver(() => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        const c = map.getCenter();
        map.relayout();
        map.setCenter(c); // 폭 변화로 인한 중심 시프트 복원
      });
    });
    ro.observe(el);
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
    };
  }, [ready]);

  useEffect(() => {
    renderMarkers();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [feed, selectedId, ready]);

  // E5-2: 채팅 단지 참조 클릭 → setCenter(panTo). nonce 변경마다 발화(동일 좌표 재클릭도 이동).
  useEffect(() => {
    const maps = mapsRef.current;
    const map = mapRef.current;
    if (!maps || !map || !panTo) return;
    map.setCenter(new maps.LatLng(panTo.lat, panTo.lng));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [panTo?.nonce, ready]);

  return (
    <div data-testid="map-container" style={{ position: "absolute", inset: 0 }}>
      <div ref={containerRef} style={{ position: "absolute", inset: 0 }} />
      {loading && (
        <div className="loading" data-testid="map-loading">
          불러오는 중…
        </div>
      )}
      <div className="legend" data-testid="legend">
        <div className="t">평당가</div>
        <div className="bar">
          {Array.from({ length: TIER_COUNT }, (_, i) => (
            <i key={i} style={{ background: `var(--t${i + 1})` }} />
          ))}
        </div>
        <div className="sc">
          <span>낮음</span>
          <span>높음</span>
        </div>
      </div>
      {unavailable && (
        <div className="map-placeholder" data-testid="map-placeholder">
          <div style={{ maxWidth: 340 }}>
            <p style={{ fontSize: 13, color: "var(--ink2)" }}>
              지도 키가 없어 지도를 표시할 수 없습니다.
              <br />
              <code className="mono" style={{ color: "var(--brand-ink)" }}>
                NEXT_PUBLIC_KAKAO_JS_KEY
              </code>
              를 설정하면 실지도가 뜹니다.
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
