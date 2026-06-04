"""비-아파트 geocode 주소 보정 마이그(in-place) — 동명중복 해소·멱등·lat NULL화·아파트 무접촉."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from app.store.db import get_connection, init_db

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from fix_nonapt_geocode_addr import fix_addresses  # noqa: E402


def _conn() -> sqlite3.Connection:
    conn = get_connection(":memory:")
    init_db(conn)
    return conn


def _seed(conn, complex_id, pt, road_addr, *, lat=None) -> None:
    conn.execute(
        "INSERT INTO complex (complex_id, property_type, road_addr, legal_addr, lat, lng, "
        "geo_source) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (complex_id, pt, road_addr, road_addr, lat, lat, "x" if lat else None),
    )
    conn.commit()


_BUSAN_JUNGGU = "ro:26110:영주동:510-3:화이트빌"  # 부산 중구 영주동 — 경북 영주시로 샜던 케이스


def test_prepends_sido_sigungu_from_complex_id() -> None:
    conn = _conn()
    _seed(conn, _BUSAN_JUNGGU, "rowhouse", "영주동 510-3")
    fixed, skipped = fix_addresses(conn)
    assert (fixed, skipped) == (1, 0)
    row = conn.execute(
        "SELECT road_addr, legal_addr FROM complex WHERE complex_id=?", (_BUSAN_JUNGGU,)
    ).fetchone()
    assert row["road_addr"] == "부산광역시 중구 영주동 510-3"
    assert row["legal_addr"] == "부산광역시 중구 영주동 510-3"


def test_nulls_lat_so_prior_misgeocode_re_resolves() -> None:
    conn = _conn()
    # 이전에 오지오코딩된(lat 보유) 건물 — 주소 보정 시 재geocode 대상이 되게 lat NULL화.
    _seed(conn, _BUSAN_JUNGGU, "rowhouse", "영주동 510-3", lat=36.82)
    fix_addresses(conn)
    row = conn.execute(
        "SELECT lat, lng, geo_source FROM complex WHERE complex_id=?", (_BUSAN_JUNGGU,)
    ).fetchone()
    assert row["lat"] is None and row["lng"] is None and row["geo_source"] is None


def test_idempotent_second_run_noop() -> None:
    conn = _conn()
    _seed(conn, "of:11680:역삼동:999:강남스카이", "officetel", "역삼동 999")
    f1, _ = fix_addresses(conn)
    f2, s2 = fix_addresses(conn)  # 2회차는 이미 보정 → skip
    assert f1 == 1 and f2 == 0 and s2 == 1
    assert conn.execute(
        "SELECT road_addr FROM complex WHERE complex_id='of:11680:역삼동:999:강남스카이'"
    ).fetchone()["road_addr"] == "서울특별시 강남구 역삼동 999"


def test_apartment_untouched() -> None:
    conn = _conn()
    conn.execute(
        "INSERT INTO complex (complex_id, property_type, road_addr, lat, lng) "
        "VALUES ('A1', 'apartment', '서울특별시 강남구 테헤란로 1', 37.5, 127.0)"
    )
    conn.commit()
    fixed, _ = fix_addresses(conn)
    assert fixed == 0  # 아파트는 대상 아님
    row = conn.execute("SELECT road_addr, lat FROM complex WHERE complex_id='A1'").fetchone()
    assert row["road_addr"] == "서울특별시 강남구 테헤란로 1" and row["lat"] == 37.5  # 불변


def test_unmapped_sgg_skipped() -> None:
    conn = _conn()
    _seed(conn, "ro:99999:없는동:1-1:x", "rowhouse", "없는동 1-1")
    fixed, skipped = fix_addresses(conn)
    assert (fixed, skipped) == (0, 1)  # 미매핑 → 보정 안 함(기존값 유지)
