"""P4-1 K-apt V4 풀필드 — 파싱 매핑·기존 회귀 0·welfare 파생·additive/idempotent 마이그레이션·
backfill(다른 컬럼 미손상)·랭킹 불변. 키리스(실캡처 fixture = 풀필드 앵커)."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import UTC, date, datetime

from app.derive import has_daycare, has_library, has_playground, has_senior_center
from app.search.spec import SoftSpec
from app.sources import _parse
from app.sources.kapt import parse_complex_info
from app.store.complex_repo import upsert_complex
from app.store.db import _COMPLEX_ADD_COLUMNS, _add_missing_columns, get_connection, init_db

FixtureLoader = Callable[[str], str]
FIXED_NOW = datetime(2026, 6, 2, 12, 0, tzinfo=UTC)


def _info(load_fixture: FixtureLoader):  # type: ignore[no-untyped-def]
    info = parse_complex_info(load_fixture("kapt_basis.json"), load_fixture("kapt_detail.json"))
    assert info is not None
    return info


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}


# ───────────────────────── 파싱 매핑 (실응답 앵커) ─────────────────────────


def test_parse_populates_basis_fullfields(load_fixture: FixtureLoader) -> None:
    info = _info(load_fixture)
    assert info.heat_type == "지역난방"  # codeHeatNm
    assert info.sale_type == "혼합"  # codeSaleNm
    assert info.mgmt_type == "위탁관리"  # codeMgrNm
    assert info.dong_count == 3  # kaptDongCnt "3"
    assert info.top_floor == 31  # kaptTopFloor
    assert info.priv_area == 36393.78  # privArea "36393.78"(str)
    assert info.mgmt_area == 51177.508  # kaptMarea(float)
    assert info.builder == "GS건설"  # kaptBcompany
    assert info.developer == "개나리6차아파트주택재건축정비사업조합"  # kaptAcompany


def test_parse_populates_detail_fullfields(load_fixture: FixtureLoader) -> None:
    info = _info(load_fixture)
    assert info.mgmt_staff == 6  # kaptMgrCnt
    assert info.security_type == "위탁관리" and info.security_staff == 6
    assert info.cleaning_staff == 5  # kaptdClcnt
    assert info.disinfection_staff == 4  # kaptdDcnt
    assert info.disinfection_method == "도포식,분무식,독이식"  # disposalType
    assert info.garbage_type == "음식물쓰레기종량제"  # codeGarbage
    assert info.water_supply == "부스타방식"  # codeWsupply
    assert info.electricity_contract == "단일계약"  # codeEcon
    assert info.fire_alarm == "GR형"  # codeFalarm
    assert info.internet == "유"  # codeNet
    assert info.elevator_count == 11  # kaptdEcnt
    assert info.cctv_count == 193  # kaptdCccnt
    assert info.subway_line == "2호선, 분당선"  # subwayLine
    assert info.subway_station is None  # subwayStation null → None
    assert info.subway_time == "5~10분이내"  # kaptdWtimesub (역세권)
    assert info.bus_time == "5분이내"  # kaptdWtimebus
    assert info.convenient_facility_raw is not None and "병원" in info.convenient_facility_raw
    assert info.education_facility_raw is not None and "초등학교" in info.education_facility_raw


def test_existing_parse_unchanged_regression(load_fixture: FixtureLoader) -> None:
    # 기존 파싱 회귀 0 — 이번 확장이 기존 필드를 건드리지 않는다.
    info = _info(load_fixture)
    assert info.kapt_code == "A10027474"
    assert info.approval_date == date(2016, 6, 22)
    assert info.household_count == 408
    assert info.parking_ground == 0 and info.parking_underground == 615
    assert info.parking_total == 615
    assert info.corridor_type == "혼합식"
    assert info.building_type == "철골철근콘크리트구조"
    assert info.amenities_raw is not None and "관리사무소" in info.amenities_raw


def test_sparse_fullfields_graceful_none(load_fixture: FixtureLoader) -> None:
    # 새 필드가 없는 sparse 응답 → 전부 None(graceful, drop/에러 아님).
    info = parse_complex_info(
        load_fixture("kapt_basis_sparse.json"), load_fixture("kapt_detail_sparse.json")
    )
    assert info is not None
    assert info.heat_type is None and info.builder is None and info.priv_area is None
    assert info.cctv_count is None and info.subway_line is None and info.elevator_count is None


def test_json_float_tolerant() -> None:
    assert _parse.json_float("36393.78") == 36393.78
    assert _parse.json_float(51177.508) == 51177.508
    assert _parse.json_float(3) == 3.0
    assert _parse.json_float(None) is None
    assert _parse.json_float("") is None
    assert _parse.json_float("n/a") is None
    assert _parse.json_float(True) is None  # bool 거부


# ───────────────────────── welfare 토큰 파생 (명확 패턴만) ─────────────────────────


def test_welfare_derive_from_real_amenities(load_fixture: FixtureLoader) -> None:
    # fixture amenities: "관리사무소, 노인정, 보육시설, 문고, 어린이놀이터, 커뮤니티공간 …"
    a = _info(load_fixture).amenities_raw
    assert has_daycare(a) is True  # 보육시설
    assert has_senior_center(a) is True  # 노인정
    assert has_library(a) is True  # 문고
    assert has_playground(a) is True  # 어린이놀이터


def test_welfare_derive_false_when_absent() -> None:
    for fn in (has_daycare, has_playground, has_senior_center, has_library):
        assert fn("관리사무소, 자전거보관소") is False
        assert fn(None) is False


# ───────────────────────── 적재(upsert): 새 컬럼 + 파생 ─────────────────────────


def test_upsert_writes_fullfields_and_welfare(load_fixture: FixtureLoader) -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    upsert_complex(conn, _info(load_fixture), updated_at=FIXED_NOW)
    row = conn.execute("SELECT * FROM complex WHERE complex_id='A10027474'").fetchone()
    assert row["heat_type"] == "지역난방"
    assert row["cctv_count"] == 193
    assert row["elevator_count"] == 11
    assert row["builder"] == "GS건설"
    assert row["priv_area"] == 36393.78
    assert row["subway_line"] == "2호선, 분당선"
    assert row["has_daycare"] == 1 and row["has_playground"] == 1
    assert row["has_senior_center"] == 1 and row["has_library"] == 1
    # 기존 파생 회귀 0
    assert row["has_gym"] == 0 and round(row["parking_ratio"], 4) == 1.5074


# ───────────────────────── additive·idempotent 마이그레이션 ─────────────────────────


def test_add_missing_columns_mechanism_idempotent() -> None:
    conn = get_connection(":memory:")
    conn.execute("CREATE TABLE t (id TEXT PRIMARY KEY, a TEXT)")
    conn.execute("INSERT INTO t (id, a) VALUES ('x', 'keep')")
    cols = (("b", "TEXT"), ("c", "INTEGER"))
    _add_missing_columns(conn, "t", cols)
    _add_missing_columns(conn, "t", cols)  # 두 번째도 안전(빠진 것 없음)
    assert {"id", "a", "b", "c"} == _columns(conn, "t")
    assert conn.execute("SELECT a FROM t WHERE id='x'").fetchone()["a"] == "keep"  # 데이터 보존


def test_init_db_migrates_preexisting_complex_without_new_columns() -> None:
    # 실행 중 적재가 만든 '구' complex 테이블(P4-1 컬럼 없음)을 시뮬레이트.
    conn = get_connection(":memory:")
    conn.execute(
        # lat/lng·road_addr/legal_addr·dong = base 컬럼(인덱스·sigungu/dong 백필 요구) — 실DB 동형.
        "CREATE TABLE complex "
        "(complex_id TEXT PRIMARY KEY, name TEXT, sigungu TEXT, dong TEXT, lat REAL, lng REAL, "
        "road_addr TEXT, legal_addr TEXT, has_gym BOOLEAN)"
    )
    conn.execute(
        "INSERT INTO complex (complex_id, name, lat, has_gym) VALUES ('A', '단지', 37.5, 1)"
    )
    conn.commit()

    init_db(conn)  # additive 마이그레이션 적용
    cols = _columns(conn, "complex")
    # P4-1 컬럼 전부 추가됨
    for name, _ in _COMPLEX_ADD_COLUMNS:
        assert name in cols, name
    # 기존 행/컬럼 불변 — 새 컬럼은 NULL
    row = conn.execute("SELECT * FROM complex WHERE complex_id='A'").fetchone()
    assert row["name"] == "단지" and row["lat"] == 37.5 and row["has_gym"] == 1
    assert row["heat_type"] is None and row["cctv_count"] is None

    init_db(conn)  # 재실행 idempotent — 에러 0, 스키마 동일
    assert _columns(conn, "complex") == cols


def test_init_db_idempotent_schema_stable() -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    first = _columns(conn, "complex")
    init_db(conn)
    assert _columns(conn, "complex") == first  # 2회 실행 스키마 동일


# ───────────────────────── backfill: 새 컬럼만 채우고 다른 컬럼 미손상 ─────────────────────────


def test_upsert_backfills_new_columns_preserving_unrelated(load_fixture: FixtureLoader) -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    info = _info(load_fixture)
    upsert_complex(conn, info, updated_at=FIXED_NOW)
    # 지오코딩(lat/lng/geo_*)은 upsert 대상(_COLUMNS) 밖 — 별도 패스가 채운 값을 시뮬레이트.
    conn.execute(
        "UPDATE complex SET lat=37.5, lng=127.0, geo_source='vworld' WHERE complex_id='A10027474'"
    )
    conn.commit()

    upsert_complex(conn, info, updated_at=FIXED_NOW)  # 재적재(backfill)
    row = conn.execute("SELECT * FROM complex WHERE complex_id='A10027474'").fetchone()
    assert row["lat"] == 37.5 and row["lng"] == 127.0 and row["geo_source"] == "vworld"  # 미손상
    assert row["cctv_count"] == 193 and row["heat_type"] == "지역난방"  # 새 컬럼 채워짐


# ───────────────────────── schema introspection + 랭킹 불변 ─────────────────────────


def test_schema_has_fullfield_columns() -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    cols = _columns(conn, "complex")
    assert {"heat_type", "builder", "cctv_count", "subway_line", "priv_area",
            "has_daycare", "has_library"} <= cols


def test_ranking_invariant_softspec_unchanged() -> None:
    # P4-1은 적재만이었고, P4-2a가 일반화하며 새 필드를 연결한다. 그 후에도 gym/pet 후방호환 유지.
    assert {"gym", "pet"} <= set(SoftSpec.model_fields)
