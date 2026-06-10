"""학교 거리 하드필터 통합 (school-1) — missing=KEEP·present-failing 제외·attach. 키리스(:memory:).

⚠ 핵심 규율(poi 동형): school_proximity 미적재 단지는 **제외 아니라 KEEP**. present-and-failing
(행 있고 거리 미달)만 제외. 좌표 read·school write만 → 지문/counts 불변.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.main import app, get_db
from app.school.locations import NearestResult
from app.school.store import write_school
from app.search.repo import search_complexes
from app.search.spec import HardFilterSpec

NOW = datetime(2026, 6, 10, tzinfo=UTC)


def _seed_school(conn: sqlite3.Connection) -> None:
    # C1 초 가깝(250)·중 가깝(300) / C2 초 멀(900) / C3 = 미적재(배치 안 돔)
    write_school(conn, "C1", "elem", NearestResult(250, "가까운초", "S1", 1, 2), now=NOW)
    write_school(conn, "C1", "mid", NearestResult(300, "가까운중", "S2", 1, 1), now=NOW)
    write_school(conn, "C2", "elem", NearestResult(900, "먼초", "S3", 0, 1), now=NOW)
    conn.commit()


def test_elem_filter_keeps_missing(search_db: sqlite3.Connection) -> None:
    _seed_school(search_db)
    ids = {c.complex_id for c in search_complexes(search_db, HardFilterSpec(elem_max_dist_m=300))}
    assert "C1" in ids  # 초 250 ≤ 300 → 통과
    assert "C2" not in ids  # 초 900 > 300 → present-failing 제외
    assert "C3" in ids  # 초 미적재 → **KEEP**(없는 데이터로 제외 금지)
    assert "C4" in ids  # 좌표·school 없음 → KEEP


def test_no_school_filter_unchanged(search_db: sqlite3.Connection) -> None:
    _seed_school(search_db)
    ids = {c.complex_id for c in search_complexes(search_db, HardFilterSpec())}
    assert {"C1", "C2", "C3", "C4"} <= ids  # 학교 필터 없으면 전 단지(회귀 0)


def test_mid_filter_present_failing_excluded(search_db: sqlite3.Connection) -> None:
    _seed_school(search_db)
    # 중학교 200m 이내: C1 중 300 > 200 → 제외, C2/C3/C4 중 미적재 → KEEP
    ids = {c.complex_id for c in search_complexes(search_db, HardFilterSpec(mid_max_dist_m=200))}
    assert "C1" not in ids
    assert {"C2", "C3", "C4"} <= ids


def test_combined_elem_keep_semantics(search_db: sqlite3.Connection) -> None:
    _seed_school(search_db)
    spec = HardFilterSpec(elem_max_dist_m=300, mart_count_1km_min=None)
    ids = {c.complex_id for c in search_complexes(search_db, spec)}
    assert ids >= {"C1", "C3", "C4"} and "C2" not in ids


# ── 라우트 attach (카드에 school 노출) ──
@pytest.fixture
def client(search_db: sqlite3.Connection) -> Iterator[TestClient]:
    write_school(search_db, "C1", "elem", NearestResult(250, "가까운초", "S1", 1, 2),
                 now=datetime.now(UTC))

    def _db() -> Iterator[sqlite3.Connection]:
        yield search_db

    app.dependency_overrides[get_db] = _db
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_route_attaches_school(client: TestClient) -> None:
    resp = client.post("/complexes/search", json={})
    assert resp.status_code == 200
    by_id = {c["complex_id"]: c for c in resp.json()}
    c1 = {s["level"]: s for s in by_id["C1"]["school"]}
    assert c1["elem"]["nearest_dist_m"] == 250 and c1["elem"]["label"] == "초등학교"
    assert c1["elem"]["nearest_name"] == "가까운초"
    assert by_id["C3"]["school"] == []  # 미적재 → computed-or-dash 빈 리스트
