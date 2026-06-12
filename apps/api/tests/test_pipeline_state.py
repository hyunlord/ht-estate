"""pipeline-state: 자기서술 원장 — UPSERT·introduced_at write-once·provenance 유도·canonical 무접촉.

핵심 불변: pipeline_state만 write(canonical COUNT/MIN read-only) → counts/지문 불변 · introduced_at
한 번만 세팅 후 보존(출생일·birth-vs-wipe 차단) · metric으로 current/target 의미 자가문서.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from app.store.db import get_connection, init_db
from app.store.pipeline_state import (
    bootstrap_pipeline_state,
    read_pipeline_state,
    record_pipeline_state,
)

T0 = datetime(2026, 6, 9, 6, 0, tzinfo=UTC)
T1 = datetime(2026, 6, 12, 6, 0, tzinfo=UTC)


def _db() -> sqlite3.Connection:
    conn = get_connection(":memory:")
    init_db(conn)  # init_db가 pipeline_state를 부트스트랩 → 각 테스트는 클린 슬레이트서 시작
    conn.execute("DELETE FROM pipeline_state")
    conn.commit()
    return conn


def _counts(conn: sqlite3.Connection) -> tuple[int, int, int]:
    return (
        conn.execute("SELECT COUNT(*) FROM complex").fetchone()[0],
        conn.execute('SELECT COUNT(*) FROM "transaction"').fetchone()[0],
        conn.execute("SELECT COUNT(*) FROM rent_transaction").fetchone()[0],
    )


def _get(conn: sqlite3.Connection, name: str) -> dict[str, object]:
    return next(r for r in read_pipeline_state(conn) if r["name"] == name)


# ── UPSERT + write-once introduced_at ──
def test_upsert_inserts_then_updates_preserving_introduced_at() -> None:
    conn = _db()
    record_pipeline_state(conn, "poi_proximity", target=100, current=20, added=20,
                          status="filling", metric="distinct complex_id with POI",
                          introduced_at_default=T0, now=T0)
    row = _get(conn, "poi_proximity")
    assert row["introduced_at"] == T0.isoformat() and row["current_count"] == 20

    # 2번째 호출 — current/status 갱신, introduced_at은 보존(다른 default 줘도 무시)
    record_pipeline_state(conn, "poi_proximity", target=100, current=55, added=35,
                          status="filling", metric="distinct complex_id with POI",
                          introduced_at_default=T1, now=T1)
    row = _get(conn, "poi_proximity")
    assert row["introduced_at"] == T0.isoformat()  # ★ write-once — 출생일 보존
    assert row["current_count"] == 55 and row["last_run_added"] == 35
    assert conn.execute("SELECT COUNT(*) FROM pipeline_state WHERE name='poi_proximity'") \
        .fetchone()[0] == 1  # 단일 행(멱등 UPSERT)


def test_metric_target_current_roundtrip() -> None:
    conn = _db()
    record_pipeline_state(conn, "ledger_enrich", target=172879, current=40965, added=4262,
                          status="filling", metric="complex with building-ledger",
                          introduced_at_default=T0, now=T1)
    r = _get(conn, "ledger_enrich")
    assert r["target_count"] == 172879 and r["current_count"] == 40965
    assert r["metric"] == "complex with building-ledger" and r["status"] == "filling"


def test_eta_for_filling() -> None:
    # 출생 T0(06-09)·3일 경과·current 36801/172670 → 평균율로 미래 ETA 산출.
    conn = _db()
    record_pipeline_state(conn, "poi_proximity", target=172670, current=36801, added=9000,
                          status="filling", metric="distinct complex_id with POI",
                          introduced_at_default=T0, now=T1)
    eta = _get(conn, "poi_proximity")["expected_complete_at"]
    assert eta is not None and str(eta) > T1.isoformat()  # 미래 시점


def test_complete_has_no_eta() -> None:
    conn = _db()
    record_pipeline_state(conn, "school_distance", target=100, current=100, added=0,
                          status="complete", metric="x", introduced_at_default=T0, now=T1)
    assert _get(conn, "school_distance")["expected_complete_at"] is None


# ── provenance 부트스트랩 (introduced_at = MIN(fetched_at)) ──
def test_bootstrap_derives_introduced_at_from_provenance() -> None:
    conn = _db()
    # 지오코딩 단지 2 + POI 행(fetched_at 최소 = 출생). 행 타임스탬프서 출생 유도.
    conn.executemany(
        "INSERT INTO complex (complex_id, name, property_type, lat, lng) "
        "VALUES (?, ?, 'apartment', 37.5, 127.0)",
        [("A", "A"), ("B", "B")],
    )
    conn.executemany(
        "INSERT INTO poi_proximity (complex_id, category, fetched_at, source) "
        "VALUES (?, 'SW8', ?, 'kakao_local')",
        [("A", "2026-06-09T06:51:00+00:00"), ("B", "2026-06-10T01:00:00+00:00")],
    )
    conn.commit()
    bootstrap_pipeline_state(conn, now=T1)
    poi = _get(conn, "poi_proximity")
    assert poi["introduced_at"] == "2026-06-09T06:51:00+00:00"  # ★ MIN(fetched_at)=출생
    assert poi["current_count"] == 2 and poi["target_count"] == 2
    assert poi["metric"] == "distinct complex_id with POI"  # rows-vs-distinct 자가문서
    assert poi["status"] == "complete"  # 2/2


def test_bootstrap_records_all_pipelines() -> None:
    conn = _db()
    bootstrap_pipeline_state(conn, now=T1)
    names = {r["name"] for r in read_pipeline_state(conn)}
    assert {
        "ingest_txn", "ingest_rent", "poi_proximity", "ledger_enrich",
        "school_distance", "school_assignment", "sigungu_backfill", "dong_backfill",
        "gym_pet", "e3_rag_corpus",
    } <= names


def test_on_demand_status() -> None:
    conn = _db()
    bootstrap_pipeline_state(conn, now=T1)
    assert _get(conn, "gym_pet")["status"] == "on_demand"
    assert _get(conn, "e3_rag_corpus")["status"] == "on_demand"


# ── ★ canonical 무접촉(counts 불변) ──
def test_record_does_not_touch_canonical() -> None:
    conn = _db()
    conn.execute(
        "INSERT INTO complex (complex_id, name, property_type) VALUES ('A','A','apartment')")
    conn.execute(
        'INSERT INTO "transaction" (txn_id, complex_id, deal_date) VALUES (?,?,?)',
        ("t1", "A", "2026-05-01"),
    )
    conn.commit()
    before = _counts(conn)
    record_pipeline_state(conn, "poi_proximity", target=1, current=0, added=0,
                          status="filling", metric="x", introduced_at_default=T0, now=T1)
    bootstrap_pipeline_state(conn, now=T1)
    bootstrap_pipeline_state(conn, now=T1)  # 멱등 재실행
    assert _counts(conn) == before  # canonical(complex/txn/rent) 불변


def test_bootstrap_idempotent_single_row_each() -> None:
    conn = _db()
    bootstrap_pipeline_state(conn, now=T0)
    bootstrap_pipeline_state(conn, now=T1)
    # 각 파이프라인 단일 행(중복 0)
    dupes = conn.execute(
        "SELECT name, COUNT(*) c FROM pipeline_state GROUP BY name HAVING c > 1"
    ).fetchall()
    assert dupes == []


# ── GET /pipeline-state 엔드포인트(read-only) ──
def test_pipeline_state_endpoint() -> None:
    from collections.abc import Iterator

    from fastapi.testclient import TestClient

    from app.main import app, get_db

    conn = _db()
    bootstrap_pipeline_state(conn, now=T1)

    def _override() -> Iterator[sqlite3.Connection]:
        yield conn

    app.dependency_overrides[get_db] = _override
    try:
        resp = TestClient(app).get("/pipeline-state")
        assert resp.status_code == 200
        pipelines = resp.json()["pipelines"]
        names = {p["name"] for p in pipelines}
        assert "poi_proximity" in names  # 자기서술 행 반환
        poi = next(p for p in pipelines if p["name"] == "poi_proximity")
        assert {"introduced_at", "target_count", "current_count", "metric", "status"} <= set(poi)
    finally:
        app.dependency_overrides.clear()
