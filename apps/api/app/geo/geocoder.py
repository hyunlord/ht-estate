"""실시간 지오코딩 — Kakao Local 주소검색 (개인 단계: geocode + 영구 캐시).

소스: Kakao Local 주소검색 (dapi.kakao.com/v2/local/search/address) — WGS84 직접 반환,
T0-7 카카오 지도와 키 일원화. 라이브 검증(T0-5b)으로 포맷 확정.
응답: {documents:[{x(경도), y(위도), road_address, ...}], meta:{total_count}}.

⚠️ 좌표 캐시·이후 bbox 검색은 개인 범위. 서비스화 시 약관 재확인(소유 좌표DB로 전환).
오프라인 좌표DB(EPSG:5179) 경로는 서비스화 시점에 재검토(PR #8 supersede).
"""

from __future__ import annotations

import json

import httpx

from app.sources._http import DEFAULT_TIMEOUT, fetch_text

KAKAO_ADDRESS_URL = "https://dapi.kakao.com/v2/local/search/address.json"


def parse_geocode(json_text: str) -> tuple[float, float] | None:
    """Kakao 주소검색 응답 → (lat, lng). 무결과/파싱실패는 None(억지 추정 안 함).

    x=경도(lng), y=위도(lat). 문자열로 와서 float 변환.
    """
    try:
        payload = json.loads(json_text)
    except ValueError:
        return None
    documents = payload.get("documents") if isinstance(payload, dict) else None
    if not documents:
        return None
    top = documents[0]
    try:
        lng = float(top["x"])
        lat = float(top["y"])
    except (KeyError, TypeError, ValueError):
        return None
    return lat, lng


def geocode(
    road_addr: str | None,
    *,
    api_key: str,
    client: httpx.Client | None = None,
    timeout: httpx.Timeout = DEFAULT_TIMEOUT,
) -> tuple[float, float] | None:
    """도로명주소 → (lat, lng). 빈 주소·무결과는 None. HTTP 에러는 전파(키/쿼터 문제 표면화).

    `api_key`는 Kakao REST 키(settings.get_kakao_key). 테스트는 `client`에 MockTransport
    를 주입해 라이브 호출 없이 검증한다.
    """
    if not road_addr or not road_addr.strip():
        return None
    json_text = fetch_text(
        KAKAO_ADDRESS_URL,
        {"query": road_addr},
        headers={"Authorization": f"KakaoAK {api_key}"},
        client=client,
        timeout=timeout,
    )
    return parse_geocode(json_text)
