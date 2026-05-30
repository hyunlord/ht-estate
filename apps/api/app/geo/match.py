"""주소 → 좌표 매칭. complex.road_addr를 파싱해 좌표DB 인덱스에서 룩업."""

from __future__ import annotations

from .address import key_of, parse_road_addr


def match_coord(
    road_addr: str | None,
    coord_index: dict[str, tuple[float, float]],
) -> tuple[float, float] | None:
    """road_addr → (lat, lng). 파싱 실패·무매치는 None(억지 추정 안 함)."""
    parsed = parse_road_addr(road_addr)
    if parsed is None:
        return None
    return coord_index.get(key_of(parsed))
