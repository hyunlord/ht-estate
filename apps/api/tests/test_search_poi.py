"""POI 하드필터 통합 (poi-1) — missing=KEEP·present-failing 제외·attach. 키리스(:memory:).

⚠ 핵심 규율: poi_proximity 미적재 단지는 **제외 아니라 KEEP**(없는 데이터로 거르지 않음).
present-and-failing(행 있고 미달)만 제외. 좌표 read·poi write만 → 지문/counts 불변.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.main import app, get_db
from app.poi.proximity import PoiResult
from app.poi.store import write_poi
from app.search.repo import search_complexes
from app.search.spec import HardFilterSpec

NOW = datetime(2026, 6, 9, tzinfo=UTC)


def _seed_poi(conn: sqlite3.Connection) -> None:
    # C1 역세권 가깝(300)·마트 5개 / C2 역 멀(800)·마트 1개 / C3 = POI 미적재(배치 안 돔)
    write_poi(conn, "C1", "SW8", PoiResult(300, "역삼역", 1, 2), now=NOW)
    write_poi(conn, "C1", "MT1", PoiResult(400, "이마트", 2, 5), now=NOW)
    write_poi(conn, "C2", "SW8", PoiResult(800, "대치역", 0, 1), now=NOW)
    write_poi(conn, "C2", "MT1", PoiResult(900, "홈플러스", 0, 1), now=NOW)
    conn.commit()


def test_subway_filter_keeps_missing(search_db: sqlite3.Connection) -> None:
    _seed_poi(search_db)
    spec = HardFilterSpec(subway_max_dist_m=500)
    ids = {c.complex_id for c in search_complexes(search_db, spec)}
    assert "C1" in ids  # SW8 300 ≤ 500 → 통과
    assert "C2" not in ids  # SW8 800 > 500 → present-failing 제외
    assert "C3" in ids  # SW8 미적재 → **KEEP**(없는 데이터로 제외 금지)
    assert "C4" in ids  # 좌표·POI 없음 → KEEP


def test_mart_count_filter_keeps_missing(search_db: sqlite3.Connection) -> None:
    _seed_poi(search_db)
    spec = HardFilterSpec(mart_count_1km_min=3)
    ids = {c.complex_id for c in search_complexes(search_db, spec)}
    assert "C1" in ids  # 마트 5 ≥ 3
    assert "C2" not in ids  # 마트 1 < 3 → 제외
    assert "C3" in ids and "C4" in ids  # 미적재 → KEEP


def test_no_poi_filter_unchanged(search_db: sqlite3.Connection) -> None:
    # POI 필터 안 주면 기존 동작(전 단지) — 회귀 0
    _seed_poi(search_db)
    ids = {c.complex_id for c in search_complexes(search_db, HardFilterSpec())}
    assert {"C1", "C2", "C3", "C4"} <= ids


def test_combined_subway_and_mart(search_db: sqlite3.Connection) -> None:
    _seed_poi(search_db)
    spec = HardFilterSpec(subway_max_dist_m=500, mart_count_1km_min=3)
    ids = {c.complex_id for c in search_complexes(search_db, spec)}
    assert ids >= {"C1", "C3", "C4"} and "C2" not in ids  # C1 양조건 통과·C3/C4 미적재 KEEP


# ── 라우트 attach (카드에 poi 노출) ──
@pytest.fixture
def client(search_db: sqlite3.Connection) -> Iterator[TestClient]:
    write_poi(search_db, "C1", "SW8", PoiResult(300, "역삼역", 1, 2), now=datetime.now(UTC))

    def _db() -> Iterator[sqlite3.Connection]:
        yield search_db

    app.dependency_overrides[get_db] = _db
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_route_attaches_poi(client: TestClient) -> None:
    resp = client.post("/complexes/search", json={})
    assert resp.status_code == 200
    by_id = {c["complex_id"]: c for c in resp.json()}
    c1_poi = {p["category"]: p for p in by_id["C1"]["poi"]}
    assert c1_poi["SW8"]["nearest_dist_m"] == 300 and c1_poi["SW8"]["label"] == "지하철역"
    assert by_id["C3"]["poi"] == []  # 미적재 → computed-or-dash 빈 리스트
