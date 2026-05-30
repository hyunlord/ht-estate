"""좌표계 변환 — EPSG:5179 → WGS84, 알려진 랜드마크로 앵커링."""

from __future__ import annotations

from app.geo.convert import to_wgs84


def test_seoul_city_hall_anchor() -> None:
    # 서울시청 5179(953901.2, 1952032.1) → WGS84 ≈ (37.5665, 126.9780)
    lat, lng = to_wgs84(953901.2, 1952032.1)
    assert abs(lat - 37.5665) < 0.001
    assert abs(lng - 126.9780) < 0.001


def test_gangnam_point_in_valid_range() -> None:
    # 강남 도로명주소 좌표는 lat 37.4~37.6, lng 126.9~127.2 범위여야
    lat, lng = to_wgs84(959737.5, 1944468.4)
    assert 37.4 < lat < 37.6
    assert 126.9 < lng < 127.2
