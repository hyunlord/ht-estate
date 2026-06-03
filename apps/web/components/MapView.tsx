"use client";

import { useEffect, useRef, useState } from "react";

import { markerLabel } from "@/lib/format";
import {
  KAKAO_JS_KEY,
  loadKakaoMaps,
  type KakaoCustomOverlay,
  type KakaoMap,
  type KakaoMaps,
} from "@/lib/kakao";
import type { Bbox, Candidate } from "@/lib/types";

const GANGNAM = { lat: 37.4979, lng: 127.0476 };
const DEBOUNCE_MS = 400;
// 이 줌 레벨(작을수록 확대) 이상이면 가격 마커 대신 격자 클러스터 배지로 집계(호갱노노식).
const CLUSTER_LEVEL = 7;

interface Cell {
  lat: number;
  lng: number;
  members: Candidate[];
}

// 줌 레벨별 격자 한 칸 크기(도) — 멀수록 굵게. 같은 칸의 후보를 한 클러스터로 묶는다.
function cellSize(level: number): number {
  return 0.0025 * Math.pow(2, Math.max(0, level - 3));
}

// 좌표 있는 후보를 격자 칸으로 집계 → 칸별 멤버 + 중심(평균).
function clusterCandidates(candidates: Candidate[], level: number): Cell[] {
  const size = cellSize(level);
  const cells = new Map<string, Candidate[]>();
  for (const c of candidates) {
    if (c.lat == null || c.lng == null) continue;
    const key = `${Math.floor(c.lat / size)}:${Math.floor(c.lng / size)}`;
    const bucket = cells.get(key);
    if (bucket) bucket.push(c);
    else cells.set(key, [c]);
  }
  return Array.from(cells.values()).map((members) => {
    const lat = members.reduce((s, m) => s + (m.lat ?? 0), 0) / members.length;
    const lng = members.reduce((s, m) => s + (m.lng ?? 0), 0) / members.length;
    return { lat, lng, members };
  });
}

export function MapView({
  candidates,
  selectedId,
  onBoundsChange,
  onSelect,
}: {
  candidates: Candidate[];
  selectedId: string | null;
  onBoundsChange: (bbox: Bbox) => void;
  onSelect: (candidate: Candidate) => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<KakaoMap | null>(null);
  const mapsRef = useRef<KakaoMaps | null>(null);
  const overlaysRef = useRef<KakaoCustomOverlay[]>([]);
  // 콜백/데이터는 ref로 — deps에 넣으면 맵이 매 렌더 재초기화된다. effect에서 동기 갱신.
  const candidatesRef = useRef(candidates);
  const selectedIdRef = useRef(selectedId);
  const boundsCb = useRef(onBoundsChange);
  const selectCb = useRef(onSelect);

  const [ready, setReady] = useState(false);
  const [unavailable, setUnavailable] = useState(false);

  // 렌더 중 ref 쓰기는 react-hooks 위반 → effect에서 동기(매 렌더, 마커 effect보다 먼저 선언).
  useEffect(() => {
    candidatesRef.current = candidates;
    selectedIdRef.current = selectedId;
    boundsCb.current = onBoundsChange;
    selectCb.current = onSelect;
  });

  // 가격 라벨 마커 — 대표 실거래 금액(없으면 단지명). 클릭→선택, 선택 단지면 강조(.sel).
  function priceOverlay(
    maps: KakaoMaps,
    map: KakaoMap,
    c: Candidate,
    selId: string | null,
  ): KakaoCustomOverlay {
    const el = document.createElement("div");
    el.className = c.complex_id === selId ? "price-marker sel" : "price-marker";
    el.textContent = markerLabel(c) ?? c.name ?? c.complex_id;
    el.addEventListener("click", () => selectCb.current(c));
    return new maps.CustomOverlay({
      position: new maps.LatLng(c.lat as number, c.lng as number),
      content: el,
      map,
      xAnchor: 0.5,
      yAnchor: 1,
      clickable: true,
      zIndex: c.complex_id === selId ? 5 : 1,
    });
  }

  // 마커/클러스터 재렌더 — 줌(idle) 시점에도 호출돼 클러스터를 재계산(좌표·선택은 ref에서).
  function renderMarkers() {
    const maps = mapsRef.current;
    const map = mapRef.current;
    if (!maps || !map) return;
    overlaysRef.current.forEach((o) => o.setMap(null));
    const next: KakaoCustomOverlay[] = [];
    const level = map.getLevel();
    const list = candidatesRef.current;
    const selId = selectedIdRef.current;

    if (level >= CLUSTER_LEVEL) {
      // 클러스터 배지 — 칸 멤버 1개면 개별 마커로 떨어뜨린다.
      for (const cell of clusterCandidates(list, level)) {
        if (cell.members.length === 1) {
          next.push(priceOverlay(maps, map, cell.members[0], selId));
          continue;
        }
        const el = document.createElement("div");
        el.className = "cluster-marker";
        el.textContent = String(cell.members.length);
        el.addEventListener("click", () => {
          map.setLevel(Math.max(1, level - 2));
          map.panTo(new maps.LatLng(cell.lat, cell.lng));
        });
        next.push(
          new maps.CustomOverlay({
            position: new maps.LatLng(cell.lat, cell.lng),
            content: el,
            map,
            xAnchor: 0.5,
            yAnchor: 0.5,
            clickable: true,
          }),
        );
      }
    } else {
      for (const c of list) {
        if (c.lat == null || c.lng == null) continue;
        next.push(priceOverlay(maps, map, c, selId));
      }
    }
    overlaysRef.current = next;
  }

  // SDK 로드 + 맵 초기화 (1회). 키 부재/실패면 placeholder.
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
          renderMarkers(); // 줌 변화 시 클러스터 재계산(검색 결과 변화와 독립)
          if (timer) clearTimeout(timer);
          timer = setTimeout(() => {
            const bounds = map.getBounds();
            const sw = bounds.getSouthWest();
            const ne = bounds.getNorthEast();
            boundsCb.current({
              min_lat: sw.getLat(),
              max_lat: ne.getLat(),
              min_lng: sw.getLng(),
              max_lng: ne.getLng(),
            });
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

  // 후보/선택 변화 → 마커 재렌더(ready 후).
  useEffect(() => {
    renderMarkers();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [candidates, selectedId, ready]);

  return (
    <div className="relative h-full w-full" data-testid="map-container">
      <div ref={containerRef} className="h-full w-full" />
      {unavailable && (
        <div
          data-testid="map-placeholder"
          className="absolute inset-0 z-[1] flex items-center justify-center p-6 text-center"
        >
          <div className="max-w-sm">
            <div className="eyebrow mb-3">map · kakao</div>
            <p className="text-sm text-muted">
              지도 키가 없어 지도를 표시할 수 없습니다.
              <br />
              <code className="font-mono text-cool">NEXT_PUBLIC_KAKAO_JS_KEY</code>를 설정하면
              실지도가 뜹니다.
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
