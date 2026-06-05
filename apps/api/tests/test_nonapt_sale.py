"""비-아파트(RH·Offi) 매매 적재 (P5-1b-3) — 키리스 mock. 필드·취소필터·건물합류·멱등·검색·회귀0.

라이브 헤비 적재는 P5-1b-3-run(별도). 여기선 mock 응답(프로브 확정 필드)으로 로직만 검증.
"""

from __future__ import annotations

import sqlite3

import httpx

from app.ingest import API_KEY_STAGES, STAGE_ORDER
from app.search.repo import search_complexes
from app.search.spec import HardFilterSpec
from app.sources.molit_nonapt import fetch_nonapt_sale, parse_nonapt_sale
from app.store.db import get_connection, init_db
from app.store.nonapt_repo import (
    building_key,
    ingest_nonapt_rent_month,
    ingest_nonapt_sale_month,
    make_nonapt_sale_txn_id,
    upsert_nonapt_building,
    upsert_nonapt_sale,
)


def _xml_sale(name_tag: str, rows: list[dict], extra: str = "") -> str:
    items = ""
    for r in rows:
        cancel = f"<cdealType>{r['cancel']}</cdealType>" if r.get("cancel") else ""
        items += (
            f"<item><{name_tag}>{r['name']}</{name_tag}><umdNm>{r['dong']}</umdNm>"
            f"<jibun>{r['jibun']}</jibun><excluUseAr>{r['area']}</excluUseAr>"
            f"<dealAmount>{r['amount']}</dealAmount>"
            f"<floor>{r.get('floor', 3)}</floor><buildYear>{r.get('by', 2010)}</buildYear>"
            f"<sggCd>11680</sggCd>{cancel}{extra}"
            f"<dealYear>2025</dealYear><dealMonth>4</dealMonth><dealDay>10</dealDay></item>"
        )
    return (
        "<response><header><resultCode>00</resultCode><resultMsg>OK</resultMsg></header>"
        f"<body><items>{items}</items><totalCount>{len(rows)}</totalCount>"
        "<numOfRows>100</numOfRows><pageNo>1</pageNo></body></response>"
    )


# 행복빌라 = 전월세 fixture와 동일 건물(같은 building_key로 합류 검증). dealAmount는 콤마 포함.
_RH_SALE = [{"name": "행복빌라", "dong": "역삼동", "jibun": "678-1", "area": "44.97",
             "amount": "95,000"}]
_OFFI_SALE = [{"name": "강남스카이", "dong": "역삼동", "jibun": "999", "area": "23.1",
               "amount": "35000"}]
_RH_RENT = [{"name": "행복빌라", "dong": "역삼동", "jibun": "678-1", "area": "44.97",
             "deposit": "30000", "rent": "0"}]


def _mock(xml: str) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, text=xml)))


def _conn() -> sqlite3.Connection:
    conn = get_connection(":memory:")
    init_db(conn)
    return conn


def _rh_xml(name_tag: str, rows: list[dict]) -> str:  # 전월세 mock(합류 테스트용)
    items = "".join(
        f"<item><{name_tag}>{r['name']}</{name_tag}><umdNm>{r['dong']}</umdNm>"
        f"<jibun>{r['jibun']}</jibun><excluUseAr>{r['area']}</excluUseAr>"
        f"<deposit>{r['deposit']}</deposit><monthlyRent>{r['rent']}</monthlyRent>"
        f"<floor>3</floor><buildYear>2010</buildYear><contractType>신규</contractType>"
        f"<sggCd>11680</sggCd><dealYear>2025</dealYear><dealMonth>4</dealMonth>"
        f"<dealDay>10</dealDay></item>"
        for r in rows
    )
    return (
        "<response><header><resultCode>00</resultCode><resultMsg>OK</resultMsg></header>"
        f"<body><items>{items}</items><totalCount>{len(rows)}</totalCount>"
        "<numOfRows>100</numOfRows><pageNo>1</pageNo></body></response>"
    )


# ───────────────────────── 파싱(프로브 확정 필드) ─────────────────────────


def test_parse_rh_sale_fields_dealamount_comma() -> None:
    page = parse_nonapt_sale(_xml_sale("mhouseNm", _RH_SALE), "rowhouse")
    t = page.items[0]
    assert t.property_type == "rowhouse" and t.name == "행복빌라" and t.legal_dong == "역삼동"
    assert t.jibun == "678-1" and t.net_area == 44.97
    assert t.price == 95000  # dealAmount '95,000' → 콤마 제거
    assert t.sgg_nm is None  # RH는 sggNm 없음(전월세와 동일) → building_key는 sgg_cd로


def test_parse_offi_sale_fields() -> None:
    page = parse_nonapt_sale(_xml_sale("offiNm", _OFFI_SALE, "<sggNm>강남구</sggNm>"), "officetel")
    t = page.items[0]
    assert t.property_type == "officetel" and t.name == "강남스카이" and t.price == 35000
    assert t.sgg_nm == "강남구"


def test_cancelled_deal_excluded() -> None:
    rows = [
        {"name": "정상빌라", "dong": "역삼동", "jibun": "1-1", "area": "40", "amount": "50000"},
        {"name": "취소빌라", "dong": "역삼동", "jibun": "2-2", "area": "40", "amount": "60000",
         "cancel": "O"},  # 취소거래 → 제외
    ]
    page = parse_nonapt_sale(_xml_sale("mhouseNm", rows), "rowhouse")
    assert [t.name for t in page.items] == ["정상빌라"]  # 취소건 제외


def test_all_cancelled_page_not_transient_raise() -> None:
    # 회귀(실측 46860 보성군 202602): 한 월 거래가 전부 취소 → 필터 후 0건·totalCount>0.
    # 원시 item이 있으니 transient 아님 → raise 금지, 빈 페이지 반환(그 region 적재 차단 X).
    rows = [{"name": "취소빌라", "dong": "벌교읍", "jibun": "1-1", "area": "40",
             "amount": "50000", "cancel": "O"}]
    page = parse_nonapt_sale(_xml_sale("mhouseNm", rows), "rowhouse")  # totalCount=1, 비취소 0
    assert page.items == [] and page.total_count == 1  # raise 없이 빈 페이지


def test_genuine_empty_burst_still_raises() -> None:
    # transient 가드 보존: 원시 item 0인데 totalCount>0(burst 빈응답)은 여전히 raise.
    from app.sources.errors import PublicDataError
    xml = (
        "<response><header><resultCode>00</resultCode><resultMsg>OK</resultMsg></header>"
        "<body><items></items><totalCount>5</totalCount>"
        "<numOfRows>100</numOfRows><pageNo>1</pageNo></body></response>"
    )
    import pytest
    with pytest.raises(PublicDataError):
        parse_nonapt_sale(xml, "rowhouse")


def test_fetch_sale_paginates_via_mock() -> None:
    trades = fetch_nonapt_sale("11680", "202504", kind="rowhouse", api_key="k",
                               client=_mock(_xml_sale("mhouseNm", _RH_SALE)))
    assert len(trades) == 1 and trades[0].price == 95000


# ───────────────────────── 건물 도출·연결·멱등 ─────────────────────────


def test_sale_derives_building_and_links_transaction() -> None:
    conn = _conn()
    t = parse_nonapt_sale(_xml_sale("mhouseNm", _RH_SALE), "rowhouse").items[0]
    key = upsert_nonapt_building(conn, t)
    txn = upsert_nonapt_sale(conn, t)
    upsert_nonapt_sale(conn, t)  # 멱등
    conn.commit()
    row = conn.execute(
        'SELECT complex_id, match_confidence, price, net_area FROM "transaction" WHERE txn_id=?',
        (txn,),
    ).fetchone()
    assert row["complex_id"] == key  # 확정 연결(퍼지조인 아님)
    assert row["match_confidence"] == 1.0 and row["price"] == 95000
    assert conn.execute('SELECT COUNT(*) FROM "transaction"').fetchone()[0] == 1  # 멱등


def test_sale_txn_id_deterministic() -> None:
    t = parse_nonapt_sale(_xml_sale("mhouseNm", _RH_SALE), "rowhouse").items[0]
    assert make_nonapt_sale_txn_id(t) == make_nonapt_sale_txn_id(t)


def test_ingest_sale_month_builds_and_links_idempotent() -> None:
    conn = _conn()
    xml = _xml_sale("mhouseNm", _RH_SALE)
    n1 = ingest_nonapt_sale_month(conn, "11680", "202504", kind="rowhouse", api_key="k",
                                  client=_mock(xml))
    n2 = ingest_nonapt_sale_month(conn, "11680", "202504", kind="rowhouse", api_key="k",
                                  client=_mock(xml))
    assert n1 == 1 and n2 == 1
    assert conn.execute('SELECT COUNT(*) FROM "transaction"').fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM complex").fetchone()[0] == 1


def test_sale_and_rent_join_same_building() -> None:
    # 같은 건물(행복빌라 역삼동 678-1)의 전월세+매매 → complex 1행에 합류(좌표 재사용).
    conn = _conn()
    ingest_nonapt_rent_month(conn, "11680", "202504", kind="rowhouse", api_key="k",
                             client=_mock(_rh_xml("mhouseNm", _RH_RENT)))
    ingest_nonapt_sale_month(conn, "11680", "202504", kind="rowhouse", api_key="k",
                             client=_mock(_xml_sale("mhouseNm", _RH_SALE)))
    assert conn.execute("SELECT COUNT(*) FROM complex").fetchone()[0] == 1  # 1 건물
    key = building_key(parse_nonapt_sale(_xml_sale("mhouseNm", _RH_SALE), "rowhouse").items[0])
    n_sale = conn.execute(
        'SELECT COUNT(*) FROM "transaction" WHERE complex_id=?', (key,)
    ).fetchone()[0]
    n_rent = conn.execute(
        "SELECT COUNT(*) FROM rent_transaction WHERE complex_id=?", (key,)
    ).fetchone()[0]
    assert n_sale == 1 and n_rent == 1  # 같은 건물에 매매축+전월세축 둘 다


# ───────────────────────── 검색 포함 · 회귀 ─────────────────────────


def test_search_includes_nonapt_sale() -> None:
    conn = _conn()
    ingest_nonapt_sale_month(conn, "11680", "202504", kind="rowhouse", api_key="k",
                             client=_mock(_xml_sale("mhouseNm", _RH_SALE)))
    # property_type=rowhouse + deal_type=sale + price_min → 매매 거래 EXISTS 요구
    rows = search_complexes(conn, HardFilterSpec(property_type="rowhouse", deal_type="sale",
                                                 price_min=1))
    assert len(rows) == 1 and rows[0].name == "행복빌라"
    # 아파트 필터로는 안 잡힘(비-아파트만)
    assert search_complexes(conn, HardFilterSpec(property_type="apartment", deal_type="sale",
                                                 price_min=1)) == []


def test_nonapt_sale_in_stage_order_and_key_stages() -> None:
    assert "nonapt_sale" in STAGE_ORDER
    assert "nonapt_sale" in API_KEY_STAGES  # data.go.kr 키 필요(승인됨)
