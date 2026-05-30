"use client";

import { useEffect, useRef, useState } from "react";

import {
  KAKAO_JS_KEY,
  loadKakaoMaps,
  type KakaoMap,
  type KakaoMaps,
  type KakaoMarker,
} from "@/lib/kakao";
import type { Bbox, Candidate } from "@/lib/types";

const GANGNAM = { lat: 37.4979, lng: 127.0476 };
const DEBOUNCE_MS = 400;

export function MapView({
  candidates,
  onBoundsChange,
  onSelect,
}: {
  candidates: Candidate[];
  onBoundsChange: (bbox: Bbox) => void;
  onSelect: (candidate: Candidate) => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<KakaoMap | null>(null);
  const mapsRef = useRef<KakaoMaps | null>(null);
  const markersRef = useRef<KakaoMarker[]>([]);
  // 콜백은 ref로 — deps에 넣으면 맵이 매 렌더 재초기화된다. 갱신은 effect에서.
  const boundsCb = useRef(onBoundsChange);
  const selectCb = useRef(onSelect);
  useEffect(() => {
    boundsCb.current = onBoundsChange;
    selectCb.current = onSelect;
  });

  const [ready, setReady] = useState(false);
  const [unavailable, setUnavailable] = useState(false);

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
  }, []);

  // 후보 → 마커 재렌더
  useEffect(() => {
    const maps = mapsRef.current;
    const map = mapRef.current;
    if (!maps || !map) return;
    markersRef.current.forEach((marker) => marker.setMap(null));
    const next: KakaoMarker[] = [];
    for (const candidate of candidates) {
      if (candidate.lat == null || candidate.lng == null) continue;
      const marker = new maps.Marker({
        position: new maps.LatLng(candidate.lat, candidate.lng),
        map,
      });
      maps.event.addListener(marker, "click", () => selectCb.current(candidate));
      next.push(marker);
    }
    markersRef.current = next;
  }, [candidates, ready]);

  return (
    <div className="relative h-full w-full" data-testid="map-container">
      <div ref={containerRef} className="h-full w-full" />
      {unavailable && (
        <div
          data-testid="map-placeholder"
          className="absolute inset-0 flex items-center justify-center bg-zinc-100 p-4 text-center text-sm text-zinc-500"
        >
          지도 키가 없어 지도를 표시할 수 없습니다. NEXT_PUBLIC_KAKAO_JS_KEY를 설정하세요.
        </div>
      )}
    </div>
  );
}
