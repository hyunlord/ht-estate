// Kakao Maps SDK 최소 타입 + 로더. 키 부재시 호출부가 placeholder로 graceful 처리.

export interface KakaoLatLng {
  getLat(): number;
  getLng(): number;
}

export interface KakaoBounds {
  getSouthWest(): KakaoLatLng;
  getNorthEast(): KakaoLatLng;
}

export interface KakaoMap {
  getBounds(): KakaoBounds;
  setCenter(latlng: KakaoLatLng): void;
}

export interface KakaoMarker {
  setMap(map: KakaoMap | null): void;
}

export interface KakaoMaps {
  Map: new (
    container: HTMLElement,
    options: { center: KakaoLatLng; level: number },
  ) => KakaoMap;
  LatLng: new (lat: number, lng: number) => KakaoLatLng;
  Marker: new (options: { position: KakaoLatLng; map?: KakaoMap }) => KakaoMarker;
  load: (callback: () => void) => void;
  event: {
    addListener: (target: object, type: string, handler: () => void) => void;
  };
}

interface KakaoWindow extends Window {
  kakao?: { maps: KakaoMaps };
}

export const KAKAO_JS_KEY = process.env.NEXT_PUBLIC_KAKAO_JS_KEY ?? "";

/** SDK 로드 후 maps 네임스페이스 resolve. 키 없으면 즉시 reject(호출부가 placeholder). */
export function loadKakaoMaps(appKey: string): Promise<KakaoMaps> {
  return new Promise((resolve, reject) => {
    if (!appKey) {
      reject(new Error("kakao js key 없음"));
      return;
    }
    const win = window as KakaoWindow;
    const existing = win.kakao?.maps;
    if (existing) {
      existing.load(() => resolve(existing));
      return;
    }
    const script = document.createElement("script");
    script.src = `//dapi.kakao.com/v2/maps/sdk.js?appkey=${appKey}&autoload=false`;
    script.async = true;
    script.onload = () => {
      const loaded = (window as KakaoWindow).kakao;
      if (loaded) {
        loaded.maps.load(() => resolve(loaded.maps));
      } else {
        reject(new Error("kakao sdk 로드 실패"));
      }
    };
    script.onerror = () => reject(new Error("kakao sdk 네트워크 실패"));
    document.head.appendChild(script);
  });
}
