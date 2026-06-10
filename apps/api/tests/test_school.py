"""학교 거리 근접 (school-1) — 로더(운영필터·level·좌표sanity)·Haversine·인덱스·러너·store. 키리스.

실 HTTP 0(오프라인 CSV fixture). 좌표 read·school_proximity write만 → 지문/counts 불변.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.school.locations import (
    LEVEL_ORDER,
    SchoolIndex,
    haversine_m,
    load_schools,
)
from app.school.runner import enrich_school
from app.school.store import done_levels, read_school, write_school
from app.store.db import get_connection, init_db

NOW = datetime(2026, 6, 10, tzinfo=UTC)

_CSV = """학교ID,학교명,학교급구분,운영상태,위도,경도
S1,가나초등학교,초등학교,운영,37.5000,127.0000
S2,가나중학교,중학교,운영,37.5010,127.0010
S3,폐교된초,초등학교,폐교,37.5000,127.0000
S4,특수학교,특수학교,운영,37.5000,127.0000
S5,먼초등학교,초등학교,운영,37.6000,127.1000
S6,해외분교,초등학교,운영,1.0000,1.0000
"""


def _write_csv(tmp_path: Path) -> str:
    p = tmp_path / "school_locations.csv"
    p.write_text(_CSV, encoding="utf-8-sig")  # data.go.kr CSV는 BOM 흔함
    return str(p)


# ── 로더 ──
def test_load_filters_operating_level_and_bbox(tmp_path: Path) -> None:
    schools = load_schools(_write_csv(tmp_path))
    ids = {s.school_id for s in schools}
    assert ids == {"S1", "S2", "S5"}  # S3 폐교·S4 특수급·S6 bbox밖 제외
    assert {s.level for s in schools} == {"elem", "mid"}  # 초→elem·중→mid
    s1 = next(s for s in schools if s.school_id == "S1")
    assert s1.level == "elem" and abs(s1.lat - 37.5) < 1e-9


# ── Haversine ──
def test_haversine_known_distance() -> None:
    # 위도 0.001° ≈ 111m. 두 점 (37.5,127.0)-(37.501,127.0)
    d = haversine_m(37.5, 127.0, 37.501, 127.0)
    assert 105 <= d <= 116


# ── 인덱스 (최근접·개수·level 부재) ──
def test_index_nearest_and_counts(tmp_path: Path) -> None:
    idx = SchoolIndex(load_schools(_write_csv(tmp_path)))
    elem = idx.nearest("elem", 37.5, 127.0)  # S1 동일좌표(0m), S5 ~13km
    assert elem.nearest_school_id == "S1" and elem.nearest_dist_m == 0
    assert elem.count_500m == 1 and elem.count_1km == 1  # S5는 1km 밖
    mid = idx.nearest("mid", 37.5, 127.0)  # S2 ~140m
    assert mid.nearest_school_id == "S2" and 100 <= (mid.nearest_dist_m or 0) <= 200
    high = idx.nearest("high", 37.5, 127.0)  # 고등 학교 0개
    assert high.nearest_dist_m is None and high.nearest_name is None


def test_index_rural_expand_finds_far_school(tmp_path: Path) -> None:
    # ±2 윈도(~3.5km) 밖에만 학교 → 셸 확장으로 최근접 찾되 1km 카운트 0
    idx = SchoolIndex(load_schools(_write_csv(tmp_path)))
    far = idx.nearest("elem", 37.40, 126.90)  # S1/S5에서 ~13km+
    assert far.nearest_dist_m is not None and far.nearest_dist_m > 1000
    assert far.count_1km == 0


# ── 러너 (resume·단지커밋) ──
@pytest.fixture
def db() -> sqlite3.Connection:
    conn = get_connection(":memory:")
    init_db(conn)
    conn.executemany(
        "INSERT INTO complex (complex_id, name, property_type, lat, lng) VALUES (?,?,?,?,?)",
        [("C1", "가", "apartment", 37.5, 127.0), ("C2", "나", "officetel", 37.501, 127.001),
         ("C3", "다", "rowhouse", None, None)],  # C3 무좌표 → 대상 아님
    )
    conn.commit()
    return conn


def _index(tmp_path: Path) -> SchoolIndex:
    return SchoolIndex(load_schools(_write_csv(tmp_path)))


def test_runner_writes_all_levels(tmp_path: Path, db: sqlite3.Connection) -> None:
    r = enrich_school(db, _index(tmp_path), now=NOW, limit=10)
    assert r["complexes"] == 2  # C1,C2 (C3 무좌표 제외)
    assert r["rows"] == 2 * len(LEVEL_ORDER)
    assert done_levels(db, "C1") == set(LEVEL_ORDER)  # 초/중/고 전부 행(고는 None이지만 적재됨)
    assert done_levels(db, "C3") == set()


def test_runner_resume_skips_done(tmp_path: Path, db: sqlite3.Connection) -> None:
    idx = _index(tmp_path)
    enrich_school(db, idx, now=NOW, limit=10)
    r2 = enrich_school(db, idx, now=NOW, limit=10)
    assert r2["complexes"] == 0 and r2["rows"] == 0  # 전부 done → skip


# ── store ──
def test_store_read_attach(tmp_path: Path, db: sqlite3.Connection) -> None:
    enrich_school(db, _index(tmp_path), now=NOW, limit=10)
    got = read_school(db, ["C1", "C3"])
    by_level = {s.level: s for s in got["C1"]}
    assert by_level["elem"].nearest_name == "가나초등학교" and by_level["elem"].label == "초등학교"
    assert by_level["high"].nearest_dist_m is None  # 고등 0개 → dash
    assert got["C3"] == []  # 미적재(무좌표) → computed-or-dash 빈 리스트


def test_write_upsert(db: sqlite3.Connection) -> None:
    from app.school.locations import NearestResult
    write_school(db, "C1", "elem", NearestResult(300, "A초", "S1", 1, 2), now=NOW)
    write_school(db, "C1", "elem", NearestResult(250, "A초", "S1", 1, 3), now=NOW)  # upsert
    got = read_school(db, ["C1"])["C1"]
    assert len(got) == 1 and got[0].nearest_dist_m == 250 and got[0].count_1km == 3
