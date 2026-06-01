"""적재 진행 원장 (C20) — 완료 월 기록/조회·멱등·0행 기록·complex 보유 판정 (키리스)."""

from __future__ import annotations

from app.store.db import get_connection, init_db
from app.store.progress_repo import completed_months, record_month, region_has_complex


def test_record_and_query_completed_months() -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    assert completed_months(conn, "transaction", "11680") == set()
    record_month(conn, "transaction", "11680", "202401", 113)
    record_month(conn, "transaction", "11680", "202402", 98)
    assert completed_months(conn, "transaction", "11680") == {"202401", "202402"}


def test_completed_months_is_scoped_by_stage_and_region() -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    record_month(conn, "transaction", "11680", "202401", 5)
    record_month(conn, "rent", "11680", "202401", 9)  # 다른 stage
    record_month(conn, "transaction", "11110", "202401", 3)  # 다른 region
    assert completed_months(conn, "transaction", "11680") == {"202401"}
    assert completed_months(conn, "rent", "11680") == {"202401"}
    assert completed_months(conn, "transaction", "11110") == {"202401"}


def test_record_month_is_idempotent() -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    record_month(conn, "transaction", "11680", "202401", 100)
    record_month(conn, "transaction", "11680", "202401", 105)  # 재기록 → 갱신, 행 중복 없음
    assert completed_months(conn, "transaction", "11680") == {"202401"}
    rows = conn.execute(
        "SELECT rows FROM ingest_progress WHERE stage='transaction' AND region='11680'"
    ).fetchall()
    assert len(rows) == 1 and rows[0][0] == 105


def test_record_zero_row_month_prevents_refetch() -> None:
    # 거래 0건인 월도 기록 → 재개 시 재fetch 안 함(빈 월/미적재 구분).
    conn = get_connection(":memory:")
    init_db(conn)
    record_month(conn, "rent", "50130", "202401", 0)
    assert completed_months(conn, "rent", "50130") == {"202401"}


def test_region_has_complex() -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    assert region_has_complex(conn, "11680") is False
    conn.execute("INSERT INTO complex (complex_id, bjd_code) VALUES ('C1', '1168010100')")
    conn.commit()
    assert region_has_complex(conn, "11680") is True  # 1168010100 → 앞5 11680
    assert region_has_complex(conn, "11110") is False
