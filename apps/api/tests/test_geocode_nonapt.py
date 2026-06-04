"""비-아파트 도심우선 바운드 geocode(geocode_nonapt_pending) — 선택·정렬·바운드·cap·아파트제외."""

from __future__ import annotations

import httpx

from app.store.db import get_connection, init_db
from app.store.geo_repo import geocode_nonapt_pending
from app.throttle import Throttle

GEO = "Kakao Local 주소검색"


def _seed(conn, complex_id, pt, road_addr, *, lat=None) -> None:  # type: ignore[no-untyped-def]
    conn.execute(
        "INSERT INTO complex (complex_id, property_type, road_addr, lat, lng) "
        "VALUES (?, ?, ?, ?, ?)",
        (complex_id, pt, road_addr, lat, lat),
    )
    conn.commit()


def _all_geocode(addr: str) -> tuple[float, float]:
    return (37.5, 127.0)  # 모든 주소 성공(좌표는 더미)


def _conn():  # type: ignore[no-untyped-def]
    conn = get_connection(":memory:")
    init_db(conn)
    return conn


def test_only_nonapt_lat_null_targeted_apartment_excluded() -> None:
    conn = _conn()
    rh = "ro:11680:역삼동:1-1:a"
    _seed(conn, rh, "rowhouse", "서울특별시 강남구 역삼동 1-1")
    _seed(conn, "A1", "apartment", "서울특별시 강남구 테헤란로 1")  # lat NULL이어도 제외
    res = geocode_nonapt_pending(conn, _all_geocode, limit=100, geo_source=GEO)
    assert res["geocoded"] == 1 and res["remaining"] == 0
    assert conn.execute("SELECT lat FROM complex WHERE complex_id='A1'").fetchone()["lat"] is None
    rh_lat = conn.execute("SELECT lat FROM complex WHERE complex_id=?", (rh,)).fetchone()["lat"]
    assert rh_lat == 37.5


def test_city_first_order_seoul_before_busan() -> None:
    conn = _conn()
    _seed(conn, "ro:26110:영주동:1-1:busan", "rowhouse", "부산광역시 중구 영주동 1-1")
    _seed(conn, "ro:11110:명륜1가:1-1:seoul", "rowhouse", "서울특별시 종로구 명륜1가 1-1")
    visited: list[str] = []

    def _track(addr: str) -> tuple[float, float]:
        visited.append(addr)
        return (37.5, 127.0)

    geocode_nonapt_pending(conn, _track, limit=1, geo_source=GEO)  # 1건만 → 도심 먼저
    assert visited == ["서울특별시 종로구 명륜1가 1-1"]  # 서울(11) < 부산(26)


def test_limit_bounds_chunk_and_reports_remaining() -> None:
    conn = _conn()
    for i in range(5):
        _seed(conn, f"ro:11680:동:{i}:b{i}", "rowhouse", f"서울특별시 강남구 동 {i}")
    res = geocode_nonapt_pending(conn, _all_geocode, limit=2, geo_source=GEO)
    assert res["geocoded"] == 2 and res["considered"] == 2 and res["remaining"] == 3


def test_resume_skips_already_geocoded() -> None:
    conn = _conn()
    for i in range(3):
        _seed(conn, f"ro:11680:동:{i}:b{i}", "rowhouse", f"서울특별시 강남구 동 {i}")
    geocode_nonapt_pending(conn, _all_geocode, limit=2, geo_source=GEO)  # 2건
    res = geocode_nonapt_pending(conn, _all_geocode, limit=10, geo_source=GEO)  # 잔여 1건만
    assert res["geocoded"] == 1 and res["remaining"] == 0


def test_cap_graceful_on_kakao_http_error() -> None:
    conn = _conn()
    for i in range(4):
        _seed(conn, f"ro:11680:동:{i}:b{i}", "rowhouse", f"서울특별시 강남구 동 {i}")
    calls = {"n": 0}

    def _quota(addr: str) -> tuple[float, float]:
        calls["n"] += 1
        if calls["n"] == 3:  # 3번째에서 Kakao 한도 모사
            raise httpx.HTTPError("quota exceeded")
        return (37.5, 127.0)

    res = geocode_nonapt_pending(conn, _quota, limit=10, geo_source=GEO)
    assert res["stopped"] == 1
    assert res["geocoded"] == 2  # 한도 전 2건은 커밋(유실 없음)
    coded = conn.execute(
        "SELECT COUNT(*) FROM complex WHERE property_type IN ('rowhouse','officetel') "
        "AND lat IS NOT NULL"
    ).fetchone()[0]
    assert coded == 2


def test_no_result_left_null_for_next_pass() -> None:
    conn = _conn()
    _seed(conn, "ro:11680:동:1:a", "rowhouse", "서울특별시 강남구 동 1")
    res = geocode_nonapt_pending(conn, lambda a: None, limit=10, geo_source=GEO)  # 무결과
    assert res["geocoded"] == 0 and res["remaining"] == 1  # NULL 유지 → 재시도 대상


def test_throttle_called_per_geocode() -> None:
    conn = _conn()
    for i in range(2):
        _seed(conn, f"ro:11680:동:{i}:b{i}", "rowhouse", f"서울특별시 강남구 동 {i}")

    class _Counting(Throttle):
        def __init__(self) -> None:
            super().__init__(0.0)
            self.calls = 0

        def wait(self) -> None:
            self.calls += 1

    t = _Counting()
    geocode_nonapt_pending(conn, _all_geocode, limit=10, geo_source=GEO, throttle=t)
    assert t.calls == 2
