"""전월세 bjd 룩업·채움·narrowing 회복 (P2-3) — (sgg,동명)→bjd 도출·bjd 조인·동명 폴백 (키리스)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime

from app.sources.molit_rent import RentTrade
from app.store.db import get_connection, init_db
from app.store.join_repo import _sgg_dong_to_bjd, backfill_matches, backfill_rent_bjd
from app.store.rent_transaction_repo import upsert_rent_transaction

NOW = datetime(2026, 6, 1, tzinfo=UTC)


def _db(*complexes: tuple[str, str, str, str, str]) -> sqlite3.Connection:
    # complex: (id, name, bjd_code, dong, legal_addr)
    conn = get_connection(":memory:")
    init_db(conn)
    conn.executemany(
        "INSERT INTO complex (complex_id, name, bjd_code, dong, legal_addr) VALUES (?,?,?,?,?)",
        complexes,
    )
    conn.commit()
    return conn


def _rent(apt: str, *, sgg: str, dong: str, jibun: str = "1") -> RentTrade:
    return RentTrade(
        apt_name=apt, legal_dong=dong, road_addr="x", build_year=2015, net_area=84.0,
        deposit=90000, monthly_rent=0, floor=5, deal_date=date(2025, 5, 1), contract_type="신규",
        sgg_cd=sgg, umd_cd=None, jibun=jibun, bonbun=None, bubun=None,
    )


def test_lookup_derives_sgg_dong_to_bjd_single_valued() -> None:
    conn = _db(
        ("A", "래미안삼성", "1168010500", "삼성동", "삼성동 1"),
        ("B", "힐스테이트종로", "1111010100", "삼성동", "삼성동 2"),  # 다른 sgg 동명
    )
    lookup = _sgg_dong_to_bjd(conn)
    assert lookup[("11680", "삼성동")] == "1168010500"
    assert lookup[("11110", "삼성동")] == "1111010100"


def test_lookup_excludes_ambiguous_sgg_dong() -> None:
    # 같은 (sgg,동명)이 두 bjd로 갈리면 모호 → 룩업 제외(억지 매핑 금지).
    conn = _db(
        ("A", "단지A", "1168010500", "삼성동", "삼성동 1"),
        ("B", "단지B", "1168010501", "삼성동", "삼성동 2"),  # 같은 sgg·동명, 다른 bjd
    )
    assert ("11680", "삼성동") not in _sgg_dong_to_bjd(conn)


def test_backfill_rent_bjd_fills_and_is_idempotent() -> None:
    conn = _db(("A", "래미안삼성", "1168010500", "삼성동", "삼성동 1"))
    upsert_rent_transaction(conn, _rent("래미안삼성", sgg="11680", dong="삼성동"), updated_at=NOW)
    assert conn.execute("SELECT bjd_code FROM rent_transaction").fetchone()[0] is None  # 적재 NULL
    stats = backfill_rent_bjd(conn)
    assert stats["filled"] == 1
    assert conn.execute("SELECT bjd_code FROM rent_transaction").fetchone()[0] == "1168010500"
    assert backfill_rent_bjd(conn)["filled"] == 0  # 멱등(이미 채움)


def test_bjd_narrowing_selects_correct_sgg_complex() -> None:
    # 동명·동일명 단지가 두 sgg에(삼성동) 있을 때 bjd narrowing이 올바른 sgg를 결정론적으로 고른다.
    # 동명 폴백은 by_dong["삼성동"]=[A,B] 둘 다 후보라 sgg를 못 가린다(오매칭 위험).
    conn = _db(
        ("A", "래미안삼성", "1168010500", "삼성동", "삼성동 1"),   # 강남 삼성동(정답 sgg)
        ("B", "래미안삼성", "1111010100", "삼성동", "삼성동 9"),   # 종로 삼성동(동명·동일명)
    )
    upsert_rent_transaction(conn, _rent("래미안삼성", sgg="11680", dong="삼성동"), updated_at=NOW)
    # bjd 채운 뒤: (11680,삼성동)→1168010500 → by_bjd 단일 후보 A → 강남 A 정확 매칭(종로 B 아님).
    backfill_rent_bjd(conn)
    stats = backfill_matches(conn, table="rent_transaction")
    assert stats["matched"] == 1
    assert conn.execute("SELECT complex_id FROM rent_transaction").fetchone()[0] == "A"


def test_lookup_miss_keeps_dong_fallback() -> None:
    # 룩업 미스(동명이 complex에 없음)면 bjd NULL 유지 → 동명 폴백 그대로(회귀 없음).
    conn = _db(("A", "역삼자이", "1168010100", "역삼동", "역삼동 1"))
    upsert_rent_transaction(conn, _rent("역삼자이", sgg="11680", dong="역삼동"), updated_at=NOW)
    upsert_rent_transaction(conn, _rent("미지단지", sgg="11680", dong="없는동", jibun="2"),
                            updated_at=NOW)
    backfill_rent_bjd(conn)
    rows = {r["legal_dong"]: r["bjd_code"]
            for r in conn.execute("SELECT legal_dong, bjd_code FROM rent_transaction")}
    assert rows["역삼동"] == "1168010100"  # 룩업 성공
    assert rows["없는동"] is None  # 미스 → NULL(동명 폴백 대상)


def test_sale_join_unaffected_by_rent_bjd() -> None:
    # 매매 회귀 0: backfill_rent_bjd는 rent_transaction만 — transaction 무관.
    conn = _db(("A", "역삼자이", "1168010100", "역삼동", "역삼동 1"))
    conn.execute(
        'INSERT INTO "transaction" (txn_id, apt_name_raw, legal_dong, bjd_code, jibun) '
        "VALUES ('T1', '역삼자이', '역삼동', '1168010100', '1')"
    )
    conn.commit()
    backfill_rent_bjd(conn)  # 매매 transaction 안 건드림
    stats = backfill_matches(conn)  # 매매 조인 — 기존대로
    assert stats["matched"] == 1
