"""배정 초등 통학구역 (school-2) — PiP·reproject(5186)·연계조인·공동·sentinel·graceful. 키리스.

합성 SHP/CSV fixture(실 geo파일 0). 좌표 read·school_assignment write만 → 지문/counts 불변.
"""
# pyshp Writer 스텁(field/poly) + 덕타이핑 _C는 reportArgumentType 오탐 → 테스트 파일 한정 억제.
# pyright: reportArgumentType=false

from __future__ import annotations

import csv as _csv
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
import shapefile
from pyproj import Transformer
from shapely.geometry import Polygon

from app.school.assignment import (
    ZoneIndex,
    attach_assignment,
    enrich_assignment,
    load_zone_index,
    read_assignment,
    write_assignment,
)
from app.store.db import get_connection, init_db

NOW = datetime(2026, 6, 10, tzinfo=UTC)
_TF = Transformer.from_crs("EPSG:4326", "EPSG:5186", always_xy=True)


def _square_5186(lat: float, lng: float, half_m: float = 500.0) -> Polygon:
    """주어진 WGS84 점을 5186로 변환해 ±half_m 정사각 폴리곤(네이티브 5186)."""
    x, y = _TF.transform(lng, lat)
    return Polygon([(x - half_m, y - half_m), (x + half_m, y - half_m),
                    (x + half_m, y + half_m), (x - half_m, y + half_m)])


# ── ZoneIndex.assign (PiP·조인·공동·경계·밖) ──
def test_assign_inside_joins_school() -> None:
    poly = _square_5186(37.50, 127.00)
    idx = ZoneIndex([("Z1", "0", poly)], {"Z1": [("S1", "역삼초")]})
    res = idx.assign(37.50, 127.00)
    assert len(res) == 1 and res[0].school_name == "역삼초" and res[0].zone_id == "Z1"
    assert res[0].is_shared is False


def test_assign_outside_returns_empty() -> None:
    idx = ZoneIndex([("Z1", "0", _square_5186(37.50, 127.00))], {"Z1": [("S1", "역삼초")]})
    assert idx.assign(35.10, 129.00) == []  # 부산 — 폴리곤 밖


def test_assign_shared_zone_multiple_schools() -> None:
    # 공동통학구역(분류1) → 복수 배정·is_shared
    poly = _square_5186(37.50, 127.00)
    idx = ZoneIndex([("Z9", "1", poly)], {"Z9": [("S1", "가초"), ("S2", "나초")]})
    res = idx.assign(37.50, 127.00)
    assert {r.school_name for r in res} == {"가초", "나초"}
    assert all(r.is_shared for r in res)


def test_assign_near_edge_inside_covered() -> None:
    # 경계 인근(안쪽) 점도 covers로 포함 (정확 경계는 부동소수 왕복오차라 안쪽 1m로 검증)
    poly = _square_5186(37.50, 127.00, half_m=300)
    idx = ZoneIndex([("Z1", "0", poly)], {"Z1": [("S1", "초")]})
    x, y = _TF.transform(127.00, 37.50)
    inv = Transformer.from_crs("EPSG:5186", "EPSG:4326", always_xy=True)
    blng, blat = inv.transform(x + 299, y)  # 우측 경계 안쪽 1m
    assert len(idx.assign(blat, blng)) == 1


# ── load_zone_index (SHP 파싱 + 연계 + graceful) ──
def _write_shp(path: Path, polys: list[tuple[str, str, Polygon]]) -> None:
    w = shapefile.Writer(str(path), shapeType=shapefile.POLYGON)
    w.field("OBJECTID", "N")
    w.field("HAKGUDO_ID", "C", size=20)
    w.field("HAKGUDO_GB", "C", size=2)
    for i, (zid, gb, poly) in enumerate(polys):
        w.record(i, zid, gb)
        ring = [list(c) for c in poly.exterior.coords]
        w.poly([ring])
    w.close()


def _write_link(path: Path, rows: list[tuple[str, str, str, str]]) -> None:
    with open(path, "w", encoding="cp949", newline="") as f:
        wr = _csv.writer(f)
        wr.writerow(["학구ID", "학교ID", "학교명", "학교급구분"])
        wr.writerows(rows)


def test_load_zone_index_from_files(tmp_path: Path) -> None:
    _write_shp(tmp_path / "z.shp", [("Z1", "0", _square_5186(37.50, 127.00))])
    _write_link(tmp_path / "link.csv", [
        ("Z1", "S1", "역삼초", "초등학교"),
        ("Z1", "M1", "역삼중", "중학교"),  # 비초등 → 제외돼야
    ])
    idx = load_zone_index(str(tmp_path / "*.shp"), str(tmp_path / "link.csv"))
    res = idx.assign(37.50, 127.00)
    assert len(res) == 1 and res[0].school_name == "역삼초"  # 초등만 조인(중학교 제외)


def test_load_zone_index_graceful_skips_empty_shape(tmp_path: Path) -> None:
    # 빈/손상 geometry 섞여도 유효분만 로드(크래시 0)
    w = shapefile.Writer(str(tmp_path / "z.shp"), shapeType=shapefile.POLYGON)
    w.field("OBJECTID", "N")
    w.field("HAKGUDO_ID", "C", size=20)
    w.field("HAKGUDO_GB", "C", size=2)
    w.record(0, "Z1", "0")
    poly = _square_5186(37.50, 127.00)
    w.poly([[list(c) for c in poly.exterior.coords]])
    w.record(1, "Z2", "0")
    w.null()  # null geometry → skip
    w.close()
    _write_link(tmp_path / "link.csv", [("Z1", "S1", "초A", "초등학교")])
    idx = load_zone_index(str(tmp_path / "*.shp"), str(tmp_path / "link.csv"))
    assert len(idx.assign(37.50, 127.00)) == 1  # 유효 폴리곤 동작·null은 skip


# ── store + runner (sentinel·missing=keep·resume) ──
@pytest.fixture
def db() -> sqlite3.Connection:
    conn = get_connection(":memory:")
    init_db(conn)
    conn.executemany(
        "INSERT INTO complex (complex_id, name, property_type, lat, lng) VALUES (?,?,?,?,?)",
        [("C1", "가", "apartment", 37.50, 127.00),   # 폴리곤 안
         ("C2", "나", "apartment", 35.10, 129.00),   # 폴리곤 밖 → none(sentinel)
         ("C3", "다", "rowhouse", None, None)],       # 무좌표 → 대상 아님
    )
    conn.commit()
    return conn


def _index() -> ZoneIndex:
    return ZoneIndex([("Z1", "0", _square_5186(37.50, 127.00))], {"Z1": [("S1", "역삼초")]})


def test_runner_assigns_and_sentinels(db: sqlite3.Connection) -> None:
    r = enrich_assignment(db, _index(), now=NOW, limit=10)
    assert r["assigned"] == 1 and r["none"] == 1 and r["processed"] == 2  # C1 배정·C2 none·C3 제외
    got = read_assignment(db, ["C1", "C2", "C3"])
    assert got["C1"][0].school_name == "역삼초"
    assert got["C2"] == []  # 폴리곤 밖 sentinel → read 제외(dash)
    assert got["C3"] == []  # 미계산


def test_runner_resume_skips_computed(db: sqlite3.Connection) -> None:
    enrich_assignment(db, _index(), now=NOW, limit=10)
    r2 = enrich_assignment(db, _index(), now=NOW, limit=10)
    assert r2["processed"] == 0  # C1(배정)·C2(sentinel) 둘 다 done → skip(무한루프 아님)


def test_attach_assignment_keeps_missing(db: sqlite3.Connection) -> None:
    enrich_assignment(db, _index(), now=NOW, limit=10)

    class _C:
        def __init__(self, cid: str) -> None:
            self.complex_id = cid
            self.assignment = None

    cands = [_C("C1"), _C("C2"), _C("C3")]
    attach_assignment(db, cands)
    assert cands[0].assignment and cands[0].assignment[0].school_name == "역삼초"
    assert cands[1].assignment == [] and cands[2].assignment == []  # 밖·미계산 → dash(keep)


def test_write_idempotent_replace(db: sqlite3.Connection) -> None:
    from app.school.assignment import Assignment
    write_assignment(db, "C1", [Assignment("Z1", "0", "S1", "초A", False)], now=NOW)
    write_assignment(db, "C1", [Assignment("Z2", "0", "S2", "초B", False)], now=NOW)  # 교체
    got = read_assignment(db, ["C1"])["C1"]
    assert len(got) == 1 and got[0].school_name == "초B"  # 멱등 교체(중복 아님)
