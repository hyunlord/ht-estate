"""좌표 백필 — lat NULL 매칭, 무매치 NULL, 멱등(영구 캐시), provenance."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from app.geo.coord_db import load_coord_db
from app.store.db import get_connection, init_db
from app.store.geo_repo import backfill_coords

FixtureLoader = Callable[[str], str]
FIXED_NOW = datetime(2026, 5, 31, 12, 0, tzinfo=UTC)
GEO_SOURCE = "행안부 위치정보요약DB 2024-03-31"


def _seed(conn) -> None:  # type: ignore[no-untyped-def]
    conn.executemany(
        "INSERT INTO complex (complex_id, name, road_addr) VALUES (?, ?, ?)",
        [
            ("C1", "역삼자이", "서울특별시 강남구 언주로 420"),  # → 매칭
            ("C2", "무주소단지", None),  # road_addr 없음 → 대상 아님
            ("C3", "안잡히는단지", "서울특별시 강남구 없는로 999"),  # 무매치
        ],
    )
    conn.commit()


def _lat(conn, cid: str):  # type: ignore[no-untyped-def]
    return conn.execute(
        "SELECT lat, lng, geo_source, geo_updated_at FROM complex WHERE complex_id=?", (cid,)
    ).fetchone()


def test_backfill_fills_matched_with_provenance(load_fixture: FixtureLoader) -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    _seed(conn)
    index = load_coord_db(load_fixture("coord_sample.txt").splitlines())

    stats = backfill_coords(conn, index, geo_source=GEO_SOURCE, updated_at=FIXED_NOW)
    # 대상은 road_addr 있는 2건(C1,C3), 그 중 1건 매칭
    assert stats == {"matched": 1, "unmatched": 1, "total": 2}

    c1 = _lat(conn, "C1")
    assert abs(c1["lat"] - 37.4986) < 0.001
    assert abs(c1["lng"] - 127.0445) < 0.001
    assert c1["geo_source"] == GEO_SOURCE  # provenance
    assert c1["geo_updated_at"] == FIXED_NOW.isoformat()

    c3 = _lat(conn, "C3")
    assert c3["lat"] is None  # 무매치는 NULL (억지 추정 안 함)


def test_backfill_is_idempotent_permanent_cache(load_fixture: FixtureLoader) -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    _seed(conn)
    index = load_coord_db(load_fixture("coord_sample.txt").splitlines())

    backfill_coords(conn, index, geo_source=GEO_SOURCE, updated_at=FIXED_NOW)
    # 재실행: lat 있는 행은 skip(영구 캐시) → 남은 무매치 1건만 재검토
    later = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
    stats = backfill_coords(conn, index, geo_source="다른DB", updated_at=later)
    assert stats == {"matched": 0, "unmatched": 1, "total": 1}
    # C1 좌표·provenance 보존(덮어쓰지 않음)
    c1 = _lat(conn, "C1")
    assert c1["geo_source"] == GEO_SOURCE
    assert c1["geo_updated_at"] == FIXED_NOW.isoformat()
