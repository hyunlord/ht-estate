"""좌표 백필 — geocode 매칭, 무결과 NULL, 멱등(영구 캐시), provenance, throttle."""

from __future__ import annotations

from datetime import UTC, datetime

from app.store.db import get_connection, init_db
from app.store.geo_repo import backfill_coords
from app.throttle import Throttle

FIXED_NOW = datetime(2026, 5, 31, 12, 0, tzinfo=UTC)
GEO_SOURCE = "Kakao Local 주소검색"

# 가짜 geocoder: 아는 주소만 좌표 반환, 나머지 None
_FAKE = {"서울특별시 강남구 언주로 420": (37.5010, 127.0442)}


def _geocode(addr: str) -> tuple[float, float] | None:
    return _FAKE.get(addr)


def _seed(conn) -> None:  # type: ignore[no-untyped-def]
    conn.executemany(
        "INSERT INTO complex (complex_id, name, road_addr) VALUES (?, ?, ?)",
        [
            ("C1", "역삼자이", "서울특별시 강남구 언주로 420"),  # geocode 성공
            ("C2", "무주소단지", None),  # road_addr 없음 → 대상 아님
            ("C3", "무결과단지", "서울특별시 강남구 없는로 999"),  # 무결과
        ],
    )
    conn.commit()


def _row(conn, cid: str):  # type: ignore[no-untyped-def]
    return conn.execute(
        "SELECT lat, lng, geo_source, geo_updated_at FROM complex WHERE complex_id=?", (cid,)
    ).fetchone()


def test_backfill_geocodes_and_fills_provenance() -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    _seed(conn)

    stats = backfill_coords(conn, _geocode, geo_source=GEO_SOURCE, updated_at=FIXED_NOW)
    assert stats == {"matched": 1, "unmatched": 1, "total": 2}  # 대상 2(C1,C3) 중 1 성공

    c1 = _row(conn, "C1")
    assert abs(c1["lat"] - 37.5010) < 0.001
    assert abs(c1["lng"] - 127.0442) < 0.001
    assert c1["geo_source"] == GEO_SOURCE
    assert c1["geo_updated_at"] == FIXED_NOW.isoformat()

    assert _row(conn, "C3")["lat"] is None  # 무결과는 NULL


def test_backfill_is_idempotent_permanent_cache() -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    _seed(conn)
    backfill_coords(conn, _geocode, geo_source=GEO_SOURCE, updated_at=FIXED_NOW)

    # 재실행: lat 있는 C1은 skip(영구 캐시) → 남은 무결과 1건만 재시도, 좌표 보존
    later = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
    stats = backfill_coords(conn, _geocode, geo_source="다른소스", updated_at=later)
    assert stats == {"matched": 0, "unmatched": 1, "total": 1}
    c1 = _row(conn, "C1")
    assert c1["geo_source"] == GEO_SOURCE  # 덮어쓰지 않음
    assert c1["geo_updated_at"] == FIXED_NOW.isoformat()


class _CountingThrottle(Throttle):
    def __init__(self) -> None:
        super().__init__(0.0)
        self.calls = 0

    def wait(self) -> None:
        self.calls += 1


def test_backfill_throttles_each_geocode() -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    _seed(conn)
    throttle = _CountingThrottle()
    backfill_coords(conn, _geocode, geo_source=GEO_SOURCE, throttle=throttle, updated_at=FIXED_NOW)
    assert throttle.calls == 2  # 대상 2건마다 wait()
