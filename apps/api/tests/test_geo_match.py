"""주소→좌표 매칭 — 히트/파싱실패/무매치 graceful."""

from __future__ import annotations

from collections.abc import Callable

from app.geo.coord_db import load_coord_db
from app.geo.match import match_coord

FixtureLoader = Callable[[str], str]


def _index(load_fixture: FixtureLoader) -> dict[str, tuple[float, float]]:
    return load_coord_db(load_fixture("coord_sample.txt").splitlines())


def test_match_hit(load_fixture: FixtureLoader) -> None:
    coord = match_coord("서울특별시 강남구 언주로 420", _index(load_fixture))
    assert coord is not None
    assert abs(coord[0] - 37.4986) < 0.001


def test_match_unmatched_address_is_none(load_fixture: FixtureLoader) -> None:
    # 좌표DB에 없는 도로명 → None (억지 추정 안 함)
    assert match_coord("서울특별시 강남구 없는로 999", _index(load_fixture)) is None


def test_match_unparseable_address_is_none(load_fixture: FixtureLoader) -> None:
    assert match_coord("서울특별시 강남구 역삼동 711-1", _index(load_fixture)) is None  # 지번주소
    assert match_coord(None, _index(load_fixture)) is None
