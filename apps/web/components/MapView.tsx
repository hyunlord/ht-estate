"use client";

import { useEffect, useRef, useState } from "react";

import { markerLabel, pricePerPyeong, tierBoundaries, tierColor, tierOf } from "@/lib/format";
import {
  KAKAO_JS_KEY,
  loadKakaoMaps,
  type KakaoCustomOverlay,
  type KakaoMap,
  type KakaoMaps,
} from "@/lib/kakao";
import type { Bbox, Candidate } from "@/lib/types";

const GANGNAM = { lat: 37.4979, lng: 127.0476 };
const DEBOUNCE_MS = 280; // spec §5.1 — idle 후 250~300ms 디바운스
const CLUSTER_LEVEL = 7; // spec §5.2 — level ≥ 7 → 클러스터

interface Cell {
  lat: number;
  lng: number;
  members: Candidate[];
}

function cellSize(level: number): number {
  return 0.0025 * Math.pow(2, Math.max(0, level - 3));
}

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
  return Array.from(cells.values()).map((members) => ({
    lat: members.reduce((s, m) => s + (m.lat ?? 0), 0) / members.length,
    lng: members.reduce((s, m) => s + (m.lng ?? 0), 0) / members.length,
    members,
  }));
}

export function MapView({
  candidates,
  selectedId,
  loading,
  onBoundsChange,
  onSelect,
}: {
  candidates: Candidate[];
  selectedId: string | null;
  loading: boolean;
  onBoundsChange: (bbox: Bbox) => void;
  onSelect: (candidate: Candidate) => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<KakaoMap | null>(null);
  const mapsRef = useRef<KakaoMaps | null>(null);
  const overlaysRef = useRef<KakaoCustomOverlay[]>([]);
  const candidatesRef = useRef(candidates);
  const selectedIdRef = useRef(selectedId);
  const boundsCb = useRef(onBoundsChange);
  const selectCb = useRef(onSelect);

  const [ready, setReady] = useState(false);
  const [unavailable, setUnavailable] = useState(false);

  useEffect(() => {
    candidatesRef.current = candidates;
    selectedIdRef.current = selectedId;
    boundsCb.current = onBoundsChange;
    selectCb.current = onSelect;
  });

  // 평당가 tier 경계(뷰포트 적응) — 마커/클러스터 색.
  function boundaries(list: Candidate[]): number[] {
    return tierBoundaries(
      list.map(pricePerPyeong).filter((v): v is number => v != null),
    );
  }

  function priceOverlay(
    maps: KakaoMaps,
    map: KakaoMap,
    c: Candidate,
    selId: string | null,
    bnd: number[],
  ): KakaoCustomOverlay {
    const wrap = document.createElement("div");
    wrap.className = c.complex_id === selId ? "mk sel" : "mk";
    const body = document.createElement("div");
    body.className = "body";
    body.style.setProperty("--mk-bg", tierColor(tierOf(pricePerPyeong(c), bnd)));
    body.textContent = markerLabel(c) ?? c.name ?? c.complex_id;
    wrap.appendChild(body);
    wrap.addEventListener("click", () => selectCb.current(c));
    return new maps.CustomOverlay({
      position: new maps.LatLng(c.lat as number, c.lng as number),
      content: wrap,
      map,
      xAnchor: 0.5,
      yAnchor: 1,
      clickable: true,
      zIndex: c.complex_id === selId ? 9 : 5,
    });
  }

  function renderMarkers() {
    const maps = mapsRef.current;
    const map = mapRef.current;
    if (!maps || !map) return;
    overlaysRef.current.forEach((o) => o.setMap(null));
    const next: KakaoCustomOverlay[] = [];
    const level = map.getLevel();
    const list = candidatesRef.current;
    const selId = selectedIdRef.current;
    const bnd = boundaries(list);

    if (level >= CLUSTER_LEVEL) {
      for (const cell of clusterCandidates(list, level)) {
        if (cell.members.length === 1) {
          next.push(priceOverlay(maps, map, cell.members[0], selId, bnd));
          continue;
        }
        const avgTier = Math.round(
          cell.members.reduce((s, m) => s + tierOf(pricePerPyeong(m), bnd), 0) /
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
      for (const c of list) {
        if (c.lat == null || c.lng == null) continue;
        next.push(priceOverlay(maps, map, c, selId, bnd));
      }
    }
    overlaysRef.current = next;
  }

  // SDK 로드 + 맵 초기화 (1회). 키 부재/실패면 placeholder. idle → auto-viewport 검색(디바운스).
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
  }, [candidates, selectedId, ready]);

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
