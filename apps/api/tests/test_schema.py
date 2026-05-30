"""스키마 introspection — 테이블 존재 + provenance 컬럼 + enrichment PK."""

from __future__ import annotations

import sqlite3

from app.store.db import get_connection, init_db


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def test_init_db_creates_canonical_tables() -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"complex", "transaction", "enrichment"} <= tables


def test_complex_provenance_columns_present() -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    cols = _columns(conn, "complex")
    # provenance + 핵심 필드
    assert {"source_url", "updated_at"} <= cols
    assert {
        "approval_date",
        "household_count",
        "corridor_type",
        "building_type",
        "parking_total",
        "parking_ground",
        "parking_underground",
        "parking_ratio",
        "amenities_raw",
        "has_gym",
    } <= cols


def test_transaction_has_match_confidence_and_provenance() -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    cols = _columns(conn, "transaction")
    assert {"complex_id", "match_confidence", "updated_at", "apt_name_raw"} <= cols


def test_enrichment_provenance_columns_present() -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    cols = _columns(conn, "enrichment")
    assert {
        "value",
        "confidence",
        "source_type",
        "source_url",
        "fetched_at",
        "ttl_expires_at",
    } <= cols


def test_enrichment_primary_key_is_complex_attribute_source() -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    info = list(conn.execute('PRAGMA table_info("enrichment")'))
    # row[5] = pk 순서(1-base, 0이면 비PK)
    pk_cols = [row[1] for row in sorted((r for r in info if r[5] > 0), key=lambda r: r[5])]
    assert pk_cols == ["complex_id", "attribute", "source_url"]


def test_init_db_is_idempotent() -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    init_db(conn)  # 두 번째 호출도 에러 없이 통과 (CREATE TABLE IF NOT EXISTS)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"complex", "transaction", "enrichment"} <= tables
