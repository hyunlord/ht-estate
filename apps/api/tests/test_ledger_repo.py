"""건축물대장 enrich 적재 불변식 — 좌표보존·UPDATE only(건물수 불변)·COALESCE·멱등·provenance.

설계 불변식(enrich-1): 대장 적재는 *속성 값* 추가지 건물 *수*·좌표 변경이 아니다.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import pytest

from app.sources.building_ledger import BuildingLedgerTitle
from app.store.db import get_connection, init_db
from app.store.ledger_repo import enrich_building, ledger_source_url

_LEDGER = BuildingLedgerTitle(
    bld_nm="대치현대빌라가동", dong_nm="가", plat_plc="서울특별시 강남구 대치동 316",
    structure="철근콘크리트구조", main_purpose="공동주택", household_count=12, ho_count=0,
    ground_floor_count=4, basement_floor_count=1, elevator_count=1, total_floor_area=1332.57,
    building_coverage_ratio=59.9, floor_area_ratio=199.5, building_height=13.5,
    approval_date="2003-08-30", ledger_pk="102411698",
)
_CID = "ro:11680:대치동:316:대치현대빌라"


@pytest.fixture
def db() -> sqlite3.Connection:
    conn = get_connection(":memory:")
    init_db(conn)
    # 비-아파트 1개 — 좌표 보유, 대장 채울 컬럼 NULL. building_type은 기존값(클로버 테스트).
    conn.execute(
        "INSERT INTO complex (complex_id, name, property_type, lat, lng, geo_source, "
        "building_type, household_count) VALUES (?, '대치현대빌라', 'rowhouse', 37.5, 127.05, "
        "'kakao:2025', '기존구조값', NULL)",
        (_CID,),
    )
    conn.commit()
    return conn


def test_enrich_preserves_geocode(db: sqlite3.Connection) -> None:
    src = ledger_source_url("11680", "10600", "0316", "0000")
    enrich_building(db, _CID, _LEDGER, source_url=src)
    row = db.execute(
        "SELECT lat, lng, geo_source FROM complex WHERE complex_id=?", (_CID,)
    ).fetchone()
    assert row["lat"] == 37.5 and row["lng"] == 127.05  # 좌표 불변(컬럼셋에 없음)
    assert row["geo_source"] == "kakao:2025"


def test_enrich_fills_null_columns(db: sqlite3.Connection) -> None:
    enrich_building(db, _CID, _LEDGER, source_url="src")
    r = db.execute(
        "SELECT main_purpose, total_floor_area, ground_floor_count, basement_floor_count, "
        "building_coverage_ratio, floor_area_ratio, building_height, household_count, "
        "top_floor, elevator_count, approval_date FROM complex WHERE complex_id=?", (_CID,)
    ).fetchone()
    assert r["main_purpose"] == "공동주택"
    assert r["total_floor_area"] == 1332.57
    assert r["ground_floor_count"] == 4
    assert r["basement_floor_count"] == 1
    assert r["building_coverage_ratio"] == 59.9
    assert r["floor_area_ratio"] == 199.5
    assert r["building_height"] == 13.5
    assert r["household_count"] == 12  # NULL이었으니 채움
    assert r["top_floor"] == 4  # 지상층수로 채움(NULL이었음)
    assert r["elevator_count"] == 1
    assert r["approval_date"] == "2003-08-30"


def test_coalesce_does_not_clobber_existing(db: sqlite3.Connection) -> None:
    # building_type은 이미 '기존구조값' — COALESCE라 대장 구조로 덮지 않음
    enrich_building(db, _CID, _LEDGER, source_url="src")
    bt = db.execute("SELECT building_type FROM complex WHERE complex_id=?", (_CID,)).fetchone()[0]
    assert bt == "기존구조값"


def test_provenance_recorded(db: sqlite3.Connection) -> None:
    when = datetime(2026, 6, 7, tzinfo=UTC)
    src = ledger_source_url("11680", "10600", "0316", "0000")
    enrich_building(db, _CID, _LEDGER, source_url=src, fetched_at=when)
    r = db.execute(
        "SELECT ledger_source_url, ledger_fetched_at, ledger_pk, ledger_bld_nm FROM complex "
        "WHERE complex_id=?", (_CID,)
    ).fetchone()
    assert "BldRgstHubService" in r["ledger_source_url"]
    assert r["ledger_fetched_at"] == when.isoformat()
    assert r["ledger_pk"] == "102411698"
    assert r["ledger_bld_nm"] == "대치현대빌라가동"


def test_update_only_no_insert(db: sqlite3.Connection) -> None:
    before = db.execute("SELECT COUNT(*) FROM complex").fetchone()[0]
    # 존재하지 않는 건물 → UPDATE 0행, INSERT 없음(건물 수 불변)
    updated = enrich_building(db, "ro:99999:없는동:1:유령빌라", _LEDGER, source_url="src")
    assert updated is False
    assert db.execute("SELECT COUNT(*) FROM complex").fetchone()[0] == before


def test_idempotent(db: sqlite3.Connection) -> None:
    src = "src"
    enrich_building(db, _CID, _LEDGER, source_url=src)
    snap1 = db.execute("SELECT * FROM complex WHERE complex_id=?", (_CID,)).fetchone()
    enrich_building(db, _CID, _LEDGER, source_url=src, fetched_at=datetime(2026, 6, 7, tzinfo=UTC))
    snap2 = db.execute("SELECT * FROM complex WHERE complex_id=?", (_CID,)).fetchone()
    # 좌표·대장값 동일(멱등) — fetched_at만 갱신 가능
    assert snap1["lat"] == snap2["lat"]
    assert snap1["main_purpose"] == snap2["main_purpose"]
    assert snap1["total_floor_area"] == snap2["total_floor_area"]
