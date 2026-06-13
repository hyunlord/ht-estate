"""unit-type-catalog — 전유부 parse·single-linkage 집계·UPSERT·catalog∪거래 병합·graceful·
canonical 무접촉(unit_type만 write)·pipeline_state·엔드포인트. 키리스(전유부 fixture 앵커)."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from datetime import UTC, datetime

from app.search.repo import unit_type_catalog
from app.search.spec import HardFilterSpec
from app.sources.building_ledger import cluster_areas, parse_exclusive_areas
from app.store.db import get_connection, init_db
from app.store.unit_type_repo import unit_types_for, upsert_unit_types

FixtureLoader = Callable[[str], str]
NOW = datetime(2026, 6, 13, tzinfo=UTC)


def _db() -> sqlite3.Connection:
    conn = get_connection(":memory:")
    init_db(conn)
    return conn


# ── 전유부 parse: 전유·주건축물만(공용/부속 제외) ──
def test_parse_exclusive_areas(load_fixture: FixtureLoader) -> None:
    areas = parse_exclusive_areas(load_fixture("ledger_exclusive.xml"))
    # 전유·주건축물 5건(66.47·66.50·84.92·84.95·26.30) — 공용(18.20)·부속주차(120.00) 제외.
    assert sorted(areas) == [26.30, 66.47, 66.50, 84.92, 84.95]


# ── single-linkage 집계: 59.94/59.97류 합침 ──
def test_cluster_areas_single_linkage(load_fixture: FixtureLoader) -> None:
    buckets = cluster_areas(parse_exclusive_areas(load_fixture("ledger_exclusive.xml")))
    # 26.3(1) · 66.47+66.50 합침(2·rep 큰값) · 84.92+84.95 합침(2). 면적순.
    assert buckets == [(26.30, 1), (66.50, 2), (84.95, 2)]


def test_cluster_areas_empty() -> None:
    assert cluster_areas([]) == []


# ── UPSERT 멱등 + read ──
def test_upsert_idempotent() -> None:
    conn = _db()
    conn.execute(
        "INSERT INTO complex (complex_id, name, property_type) VALUES ('C','C','officetel')")
    conn.commit()
    buckets = [(59.9, 100), (84.9, 50)]
    upsert_unit_types(conn, "C", buckets, source="ledger_exclusive", source_url="u", fetched_at=NOW)
    upsert_unit_types(conn, "C", [(59.9, 120), (84.9, 50)],  # 재실행(세대수 갱신)
                      source="ledger_exclusive", source_url="u2", fetched_at=NOW)
    rows = unit_types_for(conn, "C")
    assert len(rows) == 2  # 단일 행씩(중복 0)
    assert rows[0]["net_area"] == 59.9 and rows[0]["household_count"] == 120  # 갱신됨
    assert conn.execute("SELECT COUNT(*) FROM unit_type WHERE complex_id='C'").fetchone()[0] == 2


# ── ★ canonical 무접촉: enrich가 unit_type만 write ──
def test_unit_type_write_does_not_touch_canonical() -> None:
    conn = _db()
    conn.execute(
        "INSERT INTO complex (complex_id, name, property_type, lat, lng) "
        "VALUES ('C','C','officetel', 37.5, 127.0)"
    )
    conn.execute('INSERT INTO "transaction" (txn_id, complex_id, deal_date) VALUES (?,?,?)',
                 ("t1", "C", "2026-05-01"))
    conn.commit()
    before = (
        conn.execute("SELECT COUNT(*) FROM complex").fetchone()[0],
        conn.execute('SELECT COUNT(*) FROM "transaction"').fetchone()[0],
        conn.execute("SELECT lat||','||lng FROM complex WHERE complex_id='C'").fetchone()[0],
    )
    upsert_unit_types(conn, "C", [(59.9, 100)], source="ledger_exclusive",
                      source_url="u", fetched_at=NOW)
    after = (
        conn.execute("SELECT COUNT(*) FROM complex").fetchone()[0],
        conn.execute('SELECT COUNT(*) FROM "transaction"').fetchone()[0],
        conn.execute("SELECT lat||','||lng FROM complex WHERE complex_id='C'").fetchone()[0],
    )
    assert before == after  # complex/txn 카운트·좌표 불변(unit_type만 write)


# ── 병합: catalog ∪ 거래 (전 타입·거래/미거래) ──
def _seed_complex_trades(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO complex (complex_id, name, property_type) VALUES ('C','C','officetel')")
    # 66.x에 매매 2건(거래), 130에 1건(catalog 외 거래).
    conn.executemany(
        'INSERT INTO "transaction" (txn_id, complex_id, net_area, price, deal_date) '
        "VALUES (?,?,?,?,?)",
        [("t1","C",66.5,90000,"2026-05-01"), ("t2","C",66.4,88000,"2026-04-01"),
         ("t3","C",130.0,200000,"2026-03-01")],
    )
    conn.commit()


def test_merge_all_types_traded_and_untraded() -> None:
    conn = _db()
    _seed_complex_trades(conn)
    # catalog: 66.5(거래O)·84.9(미거래)·110(미거래). 130은 catalog 외 거래.
    upsert_unit_types(conn, "C", [(66.5, 100), (84.9, 50), (110.0, 20)],
                      source="ledger_exclusive", source_url="u", fetched_at=NOW)
    cat = unit_type_catalog(conn, HardFilterSpec.model_validate({"deal_type": "sale"}), "C")
    assert cat.has_catalog is True
    by_area = {round(r.net_area or 0, 0): r for r in cat.types}
    assert by_area[66.0].traded is True and by_area[66.0].household_count == 100  # 거래+세대수
    assert by_area[85.0].traded is False and by_area[85.0].household_count == 50  # 미거래+세대수
    assert by_area[110.0].traded is False and by_area[110.0].transaction_count == 0
    # catalog 외 거래(130)도 유지(거래 데이터 안 잃음)
    assert by_area[130.0].traded is True and by_area[130.0].household_count is None


def test_merge_graceful_fallback_no_catalog() -> None:
    # catalog 미적재 → has_catalog False·거래 버킷만(현 거동·무회귀).
    conn = _db()
    _seed_complex_trades(conn)
    cat = unit_type_catalog(conn, HardFilterSpec.model_validate({"deal_type": "sale"}), "C")
    assert cat.has_catalog is False
    assert all(r.traded for r in cat.types)  # 거래된 것만
    assert {round(r.net_area or 0) for r in cat.types} == {66, 130}


# ── pipeline_state 등록 ──
def test_pipeline_state_registers_unit_type() -> None:
    from app.store.pipeline_state import bootstrap_pipeline_state, read_pipeline_state
    conn = _db()
    bootstrap_pipeline_state(conn, now=NOW)
    names = {r["name"] for r in read_pipeline_state(conn)}
    assert "unit_type_catalog" in names


# ── 엔드포인트 ──
def test_unit_types_endpoint() -> None:
    from fastapi.testclient import TestClient

    from app.main import app, get_db
    conn = _db()
    _seed_complex_trades(conn)
    upsert_unit_types(conn, "C", [(66.5, 100), (84.9, 50)],
                      source="ledger_exclusive", source_url="u", fetched_at=NOW)

    def _override() -> Iterator[sqlite3.Connection]:
        yield conn

    app.dependency_overrides[get_db] = _override
    try:
        resp = TestClient(app).get("/complexes/C/unit-types?deal_type=sale")
        assert resp.status_code == 200
        body = resp.json()
        assert body["has_catalog"] is True
        areas = sorted(round(t["net_area"]) for t in body["types"])
        assert areas == [66, 85, 130]  # 거래 66·미거래 85·catalog외거래 130
    finally:
        app.dependency_overrides.clear()
