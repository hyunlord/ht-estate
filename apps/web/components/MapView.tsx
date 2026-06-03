"use client";

import { useEffect, useRef, useState } from "react";

import { markerLabelAmount, ppp, tierBoundaries, tierColor, tierOf } from "@/lib/format";
import {
  KAKAO_JS_KEY,
  loadKakaoMaps,
  type KakaoCustomOverlay,
  type KakaoMap,
  type KakaoMaps,
} from "@/lib/kakao";
import type { Bbox, MarkerCandidate } from "@/lib/types";

const GANGNAM = { lat: 37.4979, lng: 127.0476 };
const DEBOUNCE_MS = 280; // spec §5.1 — idle 후 250~300ms 디바운스
const CLUSTER_LEVEL = 6; // 도시/광역 줌 → 클러스터
const MARKER_CAP = 60; // 뷰포트 마커 캡 초과 시 줌 무관 클러스터 강제(폭주 방지)

interface Cell {
  lat: number;
  lng: number;
  members: MarkerCandidate[];
}

function cellSize(level: number): number {
  return 0.0025 * Math.pow(2, Math.max(0, level - 3));
}

function clusterMarkers(markers: MarkerCandidate[], level: number): Cell[] {
  const size = cellSize(level);
  const cells = new Map<string, MarkerCandidate[]>();
  for (const m of markers) {
    if (m.lat == null || m.lng == null) continue;
    const key = `${Math.floor(m.lat / size)}:${Math.floor(m.lng / size)}`;
    const bucket = cells.get(key);
    if (bucket) bucket.push(m);
    else cells.set(key, [m]);
  }
  return Array.from(cells.values()).map((members) => ({
    lat: members.reduce((s, m) => s + (m.lat ?? 0), 0) / members.length,
    lng: members.reduce((s, m) => s + (m.lng ?? 0), 0) / members.length,
    members,
  }));
}

export function MapView({
  markers,
  selectedId,
  loading,
  onBoundsChange,
  onSelectId,
}: {
  markers: MarkerCandidate[];
  selectedId: string | null;
  loading: boolean;
  onBoundsChange: (bbox: Bbox) => void;
  onSelectId: (complexId: string) => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<KakaoMap | null>(null);
  const mapsRef = useRef<KakaoMaps | null>(null);
  const overlaysRef = useRef<KakaoCustomOverlay[]>([]);
  const markersRef = useRef(markers);
  const selectedIdRef = useRef(selectedId);
  const boundsCb = useRef(onBoundsChange);
  const selectCb = useRef(onSelectId);

  const [ready, setReady] = useState(false);
  const [unavailable, setUnavailable] = useState(false);

  useEffect(() => {
    markersRef.current = markers;
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
    body.textContent = markerLabelAmount(m.price) ?? m.name ?? m.complex_id;
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

  function renderMarkers() {
    const maps = mapsRef.current;
    const map = mapRef.current;
    if (!maps || !map) return;
    overlaysRef.current.forEach((o) => o.setMap(null));
    const next: KakaoCustomOverlay[] = [];
    const level = map.getLevel();
    const list = markersRef.current;
    const selId = selectedIdRef.current;
    const bnd = boundaries(list);
    const coordCount = list.filter((m) => m.lat != null && m.lng != null).length;

    if (level >= CLUSTER_LEVEL || coordCount > MARKER_CAP) {
      for (const cell of clusterMarkers(list, level)) {
        if (cell.members.length === 1) {
          next.push(priceOverlay(maps, map, cell.members[0], selId, bnd));
          continue;
        }
        const avgTier = Math.round(
          cell.members.reduce((s, m) => s + tierOf(ppp(m.price, m.net_area), bnd), 0) /
            cell.members.length,
        );
        const el = document.createElement("div");
        el.className = "cluster";
        el.style.background = tierColor(avgTier);
        el.innerHTML = `<span class="n">${cell.members.length}</span><span class="t">단지</span>`;
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
      for (const m of list) {
        if (m.lat == null || m.lng == null) continue;
        next.push(priceOverlay(maps, map, m, selId, bnd));
      }
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

  useEffect(() => {
    renderMarkers();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [markers, selectedId, ready]);

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
          <i style={{ background: "var(--t1)" }} />
          <i style={{ background: "var(--t2)" }} />
          <i style={{ background: "var(--t3)" }} />
          <i style={{ background: "var(--t4)" }} />
          <i style={{ background: "var(--t5)" }} />
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
