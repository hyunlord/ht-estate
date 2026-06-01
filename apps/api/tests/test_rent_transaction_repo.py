"""rent_transaction 적재 — upsert 멱등·txn_id 결정론·rent_type·스키마 왕복·조인 재사용 (키리스)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime, timedelta

from app.sources.molit_rent import RentTrade
from app.store.db import get_connection, init_db
from app.store.join_repo import backfill_matches
from app.store.rent_transaction_repo import (
    ingest_rent_months,
    make_rent_txn_id,
    upsert_rent_transaction,
)

NOW = datetime(2026, 6, 1, tzinfo=UTC)


def _rent(
    apt: str, *, deposit: int, monthly: int, jibun: str = "670", bjd_sgg: str = "11680",
    bjd_umd: str = "10600", contract: str = "신규",
) -> RentTrade:
    return RentTrade(
        apt_name=apt, legal_dong="대치동", road_addr="삼성로", build_year=2015,
        net_area=94.49, deposit=deposit, monthly_rent=monthly, floor=12,
        deal_date=date(2025, 4, 10), contract_type=contract,
        sgg_cd=bjd_sgg, umd_cd=bjd_umd, jibun=jibun, bonbun=None, bubun=None,
    )


def _db() -> sqlite3.Connection:
    conn = get_connection(":memory:")
    init_db(conn)
    return conn


def test_upsert_persists_rent_fields() -> None:
    conn = _db()
    upsert_rent_transaction(conn, _rent("대치팰리스", deposit=180000, monthly=0), updated_at=NOW)
    row = conn.execute(
        "SELECT deposit, monthly_rent, rent_type, contract_type, complex_id FROM rent_transaction"
    ).fetchone()
    assert row["deposit"] == 180000
    assert row["monthly_rent"] == 0
    assert row["rent_type"] == "jeonse"  # 월세 0 → 전세
    assert row["contract_type"] == "신규"
    assert row["complex_id"] is None  # 조인 전


def test_monthly_rent_type() -> None:
    conn = _db()
    upsert_rent_transaction(conn, _rent("은마", deposit=5000, monthly=180), updated_at=NOW)
    assert conn.execute("SELECT rent_type FROM rent_transaction").fetchone()[0] == "monthly"


def test_txn_id_deterministic_and_distinguishes_deposit() -> None:
    a = _rent("X", deposit=180000, monthly=0)
    b = _rent("X", deposit=180000, monthly=0)
    c = _rent("X", deposit=200000, monthly=0)  # 보증금 다름 → 다른 id
    assert make_rent_txn_id(a) == make_rent_txn_id(b)
    assert make_rent_txn_id(a) != make_rent_txn_id(c)


def test_upsert_idempotent() -> None:
    conn = _db()
    t = _rent("X", deposit=180000, monthly=0)
    upsert_rent_transaction(conn, t, updated_at=NOW)
    upsert_rent_transaction(conn, t, updated_at=NOW)  # 재적재
    assert conn.execute("SELECT COUNT(*) FROM rent_transaction").fetchone()[0] == 1  # 멱등


def test_ingest_rent_months_via_injected_fetch(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    conn = _db()
    import app.store.rent_transaction_repo as repo

    monkeypatch.setattr(
        repo, "fetch_rent_trades",
        lambda *a, **k: [_rent("A", deposit=10000, monthly=50),
                         _rent("B", deposit=20000, monthly=0, jibun="316")],
    )
    n = ingest_rent_months(conn, "11680", ["202504"], api_key="dummy")
    assert n == 2
    assert conn.execute("SELECT COUNT(*) FROM rent_transaction").fetchone()[0] == 2


def test_join_reused_for_rent_transaction() -> None:
    # 퍼지조인(backfill_matches) table=rent_transaction 재사용 → complex_id 채움.
    conn = _db()
    conn.execute(
        "INSERT INTO complex (complex_id, name, bjd_code, dong, legal_addr) "
        "VALUES ('A10027800', '래미안대치팰리스', '1168010600', '대치동', '대치동 670')"
    )
    conn.commit()
    trade = _rent("래미안대치팰리스", deposit=180000, monthly=0)
    upsert_rent_transaction(conn, trade, updated_at=NOW)
    stats = backfill_matches(conn, table="rent_transaction")
    assert stats["matched"] == 1
    row = conn.execute("SELECT complex_id, match_confidence FROM rent_transaction").fetchone()
    assert row["complex_id"] == "A10027800"
    assert row["match_confidence"] is not None


def test_join_rent_via_dong_fallback_when_bjd_none() -> None:
    # 라이브 전월세 현실: umdCd 없음 → bjd_code None → 법정동명(legal_dong) narrowing 폴백.
    conn = _db()
    conn.execute(
        "INSERT INTO complex (complex_id, name, bjd_code, dong, legal_addr) "
        "VALUES ('A10027800', '래미안대치팰리스', '1168010600', '대치동', '대치동 670')"
    )
    conn.commit()
    # bjd 없는 rent 거래(sgg만, umd None) — 실 전월세 응답 형태.
    trade = RentTrade(
        apt_name="래미안대치팰리스", legal_dong="대치동", road_addr="삼성로", build_year=2015,
        net_area=94.49, deposit=180000, monthly_rent=0, floor=12, deal_date=date(2025, 5, 10),
        contract_type="신규", sgg_cd="11680", umd_cd=None, jibun="670", bonbun=None, bubun=None,
    )
    assert trade.bjd_code is None  # umdCd 없어 bjd 못 만듦
    upsert_rent_transaction(conn, trade, updated_at=NOW)
    stats = backfill_matches(conn, table="rent_transaction")
    assert stats["matched"] == 1  # 법정동명 폴백으로 매칭
    assert conn.execute(
        "SELECT complex_id FROM rent_transaction"
    ).fetchone()["complex_id"] == "A10027800"


def test_join_rejects_unknown_table() -> None:
    conn = _db()
    try:
        backfill_matches(conn, table="complex; DROP TABLE complex")
        raise AssertionError("allowlist 밖 테이블인데 통과됨")
    except ValueError:
        pass


def test_real_schema_round_trip_with_ttl_unaffected() -> None:
    # rent_transaction이 매매 transaction과 독립(별도 테이블) — 매매 적재 영향 없음.
    conn = _db()
    upsert_rent_transaction(conn, _rent("X", deposit=180000, monthly=0),
                            updated_at=NOW + timedelta(days=1))
    assert conn.execute("SELECT COUNT(*) FROM \"transaction\"").fetchone()[0] == 0  # 매매 불변
