"""좌표DB 로드 — 파이프 포맷 파싱 + 5179→WGS84 적재 + 깨진 라인 skip."""

from __future__ import annotations

from collections.abc import Callable

from app.geo.coord_db import load_coord_db

FixtureLoader = Callable[[str], str]


def test_load_coord_db_indexes_by_addr_key(load_fixture: FixtureLoader) -> None:
    index = load_coord_db(load_fixture("coord_sample.txt").splitlines())
    # 깨진 라인(컬럼 부족)은 skip → 정상 3건
    assert len(index) == 3
    assert "서울특별시|강남구|언주로|420-0" in index


def test_loaded_coords_are_wgs84(load_fixture: FixtureLoader) -> None:
    index = load_coord_db(load_fixture("coord_sample.txt").splitlines())
    lat, lng = index["서울특별시|강남구|언주로|420-0"]
    # 5179(959737.5,1944468.4) → WGS84 ≈ (37.4986, 127.0445)
    assert abs(lat - 37.4986) < 0.001
    assert abs(lng - 127.0445) < 0.001
