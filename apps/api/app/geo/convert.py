"""좌표계 변환 — 행안부 위치정보요약DB(EPSG:5179, GRS80 UTM-K) → WGS84(EPSG:4326).

Transformer는 비싸므로 모듈 레벨에서 1회 생성해 재사용한다. always_xy=True →
입출력 순서가 (경도/x, 위도/y). 반환은 (lat, lng) 순서로 뒤집어 준다.
변환 정확도는 알려진 랜드마크로 검증(test_geo_convert).
"""

from __future__ import annotations

from pyproj import Transformer

KATEC_5179 = "EPSG:5179"  # 위치정보요약DB 주출입구 좌표계 (GRS80 UTM-K)
WGS84 = "EPSG:4326"

_TO_WGS84 = Transformer.from_crs(KATEC_5179, WGS84, always_xy=True)


def to_wgs84(x: float, y: float) -> tuple[float, float]:
    """EPSG:5179 (x, y) → WGS84 (lat, lng). 단지 카드/지도가 쓰는 위경도."""
    lng, lat = _TO_WGS84.transform(x, y)
    return lat, lng
