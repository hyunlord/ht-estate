"""비-아파트(RH·Offi 전월세) 적재 (P5-1b-2) — 키리스. 검증 필드 파싱·PNU 키·멱등·거래연결·회귀0.

라이브 헤비 적재는 P0 백필 후. 여기선 mock 응답(STEP1 검증 필드)으로 로직만 검증.
"""

from __future__ import annotations

import sqlite3

import httpx

from app.ingest import STAGE_ORDER
from app.search.repo import search_complexes
from app.search.spec import HardFilterSpec
from app.sources.molit_nonapt import fetch_nonapt_rent, parse_nonapt_rent
from app.store.db import get_connection, init_db
from app.store.nonapt_repo import (
    _geocodable_addr,
    building_key,
    ingest_nonapt_rent_month,
    upsert_nonapt_building,
    upsert_nonapt_rent,
)


def _xml(name_tag: str, rows: list[dict], extra: str = "") -> str:
    items = ""
    for r in rows:
        items += (
            f"<item><{name_tag}>{r['name']}</{name_tag}><umdNm>{r['dong']}</umdNm>"
            f"<jibun>{r['jibun']}</jibun><excluUseAr>{r['area']}</excluUseAr>"
            f"<deposit>{r['deposit']}</deposit><monthlyRent>{r['rent']}</monthlyRent>"
            f"<floor>{r.get('floor', 3)}</floor><buildYear>{r.get('by', 2010)}</buildYear>"
            f"<contractType>신규</contractType><sggCd>11680</sggCd>{extra}"
            f"<dealYear>2025</dealYear><dealMonth>4</dealMonth><dealDay>10</dealDay></item>"
        )
    return (
        "<response><header><resultCode>00</resultCode><resultMsg>OK</resultMsg></header>"
        f"<body><items>{items}</items><totalCount>{len(rows)}</totalCount>"
        "<numOfRows>100</numOfRows><pageNo>1</pageNo></body></response>"
    )


_RH = [{"name": "행복빌라", "dong": "역삼동", "jibun": "678-1", "area": "44.97",
        "deposit": "30000", "rent": "0"}]
_OFFI = [{"name": "강남스카이", "dong": "역삼동", "jibun": "999", "area": "23.1",
          "deposit": "5000", "rent": "80"}]


def _mock_client(xml: str) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, text=xml)))


# ───────────────────────── 클라이언트 파싱(검증 필드) ─────────────────────────


def test_parse_rh_rent_verified_fields() -> None:
    page = parse_nonapt_rent(_xml("mhouseNm", _RH), "rowhouse")
    t = page.items[0]
    assert t.property_type == "rowhouse"
    assert t.name == "행복빌라" and t.legal_dong == "역삼동" and t.jibun == "678-1"
    assert t.net_area == 44.97 and t.deposit == 30000 and t.monthly_rent == 0
    assert t.rent_type == "jeonse"  # 월세 0


def test_parse_offi_rent_verified_fields() -> None:
    page = parse_nonapt_rent(_xml("offiNm", _OFFI, "<sggNm>강남구</sggNm>"), "officetel")
    t = page.items[0]
    assert t.property_type == "officetel" and t.name == "강남스카이"
    assert t.sgg_nm == "강남구" and t.rent_type == "monthly"  # 월세 80


def test_fetch_paginates_via_mock_client() -> None:
    client = _mock_client(_xml("mhouseNm", _RH))
    trades = fetch_nonapt_rent("11680", "202504", kind="rowhouse", api_key="k", client=client)
    assert len(trades) == 1


# ───────────────────────── PNU building_key ─────────────────────────


def test_building_key_deterministic_and_disambiguates() -> None:
    page = parse_nonapt_rent(_xml("mhouseNm", _RH), "rowhouse")
    t = page.items[0]
    assert building_key(t) == building_key(t)  # 결정론
    # 같은 지번 다른 건물명 → 다른 키(디스앰비그)
    t2 = t.model_copy(update={"name": "다른빌라"})
    assert building_key(t) != building_key(t2)
    # property_type 다르면 다른 키(연립 vs 오피스텔 동일 주소)
    t3 = t.model_copy(update={"property_type": "officetel"})
    assert building_key(t) != building_key(t3)


# ───────────────────────── 도출·연결·멱등 ─────────────────────────


def _conn() -> sqlite3.Connection:
    conn = get_connection(":memory:")
    init_db(conn)
    return conn


def test_building_derive_thin_and_idempotent() -> None:
    conn = _conn()
    page = parse_nonapt_rent(_xml("mhouseNm", _RH), "rowhouse")
    t = page.items[0]
    key = upsert_nonapt_building(conn, t)
    upsert_nonapt_building(conn, t)  # 2회 = 1행(멱등)
    conn.commit()
    row = conn.execute(
        "SELECT property_type, name, household_count, has_gym, approval_date "
        "FROM complex WHERE complex_id=?",
        (key,),
    ).fetchone()
    assert row["property_type"] == "rowhouse" and row["name"] == "행복빌라"
    assert row["household_count"] is None and row["has_gym"] is None  # K-apt 전용 = NULL(없음)
    assert row["approval_date"] == "2010-01-01"  # 연식 근사
    assert conn.execute("SELECT COUNT(*) FROM complex").fetchone()[0] == 1


def test_geocodable_addr_prepends_sido_sigungu_for_rh() -> None:
    # 회귀(geocode 동명중복): RH는 sggNm이 없어도 sggCd→'시도 시군구'로 프리픽스해야 한다.
    # (안 그러면 '역삼동 678-1'만 → 동명 중복 시 타도시 오지오코딩.)
    t = parse_nonapt_rent(_xml("mhouseNm", _RH), "rowhouse").items[0]
    assert t.sgg_nm is None  # RH는 sggNm 미제공
    assert _geocodable_addr(t) == "서울특별시 강남구 역삼동 678-1"


def test_geocodable_addr_uses_code_label_over_sggnm_for_offi() -> None:
    # Offi는 sggNm('강남구')이 있어도, 시도까지 붙는 코드 라벨('서울특별시 강남구')을 우선.
    t = parse_nonapt_rent(_xml("offiNm", _OFFI, "<sggNm>강남구</sggNm>"), "officetel").items[0]
    assert _geocodable_addr(t) == "서울특별시 강남구 역삼동 999"


def test_building_road_addr_has_sido_sigungu_prefix() -> None:
    conn = _conn()
    t = parse_nonapt_rent(_xml("mhouseNm", _RH), "rowhouse").items[0]
    key = upsert_nonapt_building(conn, t)
    conn.commit()
    row = conn.execute(
        "SELECT road_addr, legal_addr FROM complex WHERE complex_id=?", (key,)
    ).fetchone()
    assert row["road_addr"] == "서울특별시 강남구 역삼동 678-1"
    assert row["legal_addr"] == "서울특별시 강남구 역삼동 678-1"


def test_rent_linked_to_derived_building() -> None:
    conn = _conn()
    t = parse_nonapt_rent(_xml("mhouseNm", _RH), "rowhouse").items[0]
    key = upsert_nonapt_building(conn, t)
    txn = upsert_nonapt_rent(conn, t)
    upsert_nonapt_rent(conn, t)  # 멱등
    conn.commit()
    row = conn.execute(
        "SELECT complex_id, match_confidence, deposit, rent_type "
        "FROM rent_transaction WHERE txn_id=?",
        (txn,),
    ).fetchone()
    assert row["complex_id"] == key  # 같은 레코드 도출 → 확정 연결(퍼지조인 아님)
    assert row["match_confidence"] == 1.0 and row["deposit"] == 30000
    assert row["rent_type"] == "jeonse"
    assert conn.execute("SELECT COUNT(*) FROM rent_transaction").fetchone()[0] == 1


def test_ingest_month_builds_and_links_idempotent() -> None:
    conn = _conn()
    xml = _xml("mhouseNm", _RH)
    n1 = ingest_nonapt_rent_month(
        conn, "11680", "202504", kind="rowhouse", api_key="k", client=_mock_client(xml)
    )
    n2 = ingest_nonapt_rent_month(
        conn, "11680", "202504", kind="rowhouse", api_key="k", client=_mock_client(xml)
    )
    assert n1 == 1 and n2 == 1
    assert conn.execute("SELECT COUNT(*) FROM complex").fetchone()[0] == 1  # 멱등(중복 건물 없음)
    assert conn.execute("SELECT COUNT(*) FROM rent_transaction").fetchone()[0] == 1


def test_search_property_type_includes_derived_nonapt() -> None:
    conn = _conn()
    t = parse_nonapt_rent(_xml("mhouseNm", _RH), "rowhouse").items[0]
    upsert_nonapt_building(conn, t)
    conn.commit()
    # property_type 필터(P5-1a)가 도출 비-아파트를 포함.
    rows = search_complexes(conn, HardFilterSpec(property_type="rowhouse"))
    assert len(rows) == 1 and rows[0].name == "행복빌라"
    assert search_complexes(conn, HardFilterSpec(property_type="apartment")) == []  # 비-아파트만


def test_nonapt_rent_in_stage_order() -> None:
    assert "nonapt_rent" in STAGE_ORDER
    assert STAGE_ORDER.index("nonapt_rent") < STAGE_ORDER.index("geocode")  # geocode 전 → 좌표 채움
