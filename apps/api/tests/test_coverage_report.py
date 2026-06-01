"""전국 커버리지 리포트 (C20) — 집계(단지·매매·전월세·recall·geocode)·시도 rollup (seeded)."""

from __future__ import annotations

import sys
from pathlib import Path

from app.store.db import get_connection, init_db

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from coverage_report import collect_coverage, format_coverage  # noqa: E402


def _seed(conn) -> None:  # type: ignore[no-untyped-def]
    conn.executemany(
        "INSERT INTO complex (complex_id, bjd_code, road_addr, lat) VALUES (?, ?, ?, ?)",
        [
            ("C1", "1168010100", "서울 강남구 ...", 37.5),   # 강남 geocoded
            ("C2", "1168010100", "서울 강남구 ...", None),    # 강남 geocode 안됨
            ("C3", "4113510300", None, None),                 # 분당 road_addr 없음(geo_total 제외)
        ],
    )
    conn.executemany(
        'INSERT INTO "transaction" (txn_id, bjd_code, complex_id) VALUES (?, ?, ?)',
        [
            ("T1", "1168010100", "C1"),  # 강남 매칭
            ("T2", "1168010100", None),  # 강남 미매칭
            ("T3", "4113510300", "C3"),  # 분당 매칭
        ],
    )
    conn.executemany(
        "INSERT INTO rent_transaction (txn_id, sgg_cd, complex_id) VALUES (?, ?, ?)",
        [("R1", "11680", "C1"), ("R2", "11680", None)],  # 강남 전월세 2건 중 1 매칭
    )
    conn.commit()


def test_collect_coverage_aggregates_per_region() -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    _seed(conn)
    codes = [("11680", "서울특별시", "강남구"), ("41135", "경기도", "성남시분당구"),
             ("50110", "제주특별자치도", "제주시")]
    rows = {r.code: r for r in collect_coverage(conn, codes)}

    gn = rows["11680"]
    assert gn.complexes == 2 and gn.geocoded == 1 and gn.geo_total == 2
    assert gn.sale == 2 and gn.sale_matched == 1
    assert gn.rent == 2 and gn.rent_matched == 1

    bd = rows["41135"]
    assert bd.complexes == 1 and bd.sale == 1 and bd.sale_matched == 1
    assert bd.geo_total == 0  # road_addr 없음

    assert rows["50110"].complexes == 0  # 미적재 시군구도 행은 존재(목표 대비 가시화)


def test_format_coverage_shows_progress_and_totals() -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    _seed(conn)
    codes = [("11680", "서울특별시", "강남구"), ("41135", "경기도", "성남시분당구"),
             ("50110", "제주특별자치도", "제주시")]
    out = format_coverage(collect_coverage(conn, codes))
    assert "시군구 적재: 2/3" in out  # 강남·분당 적재, 제주 미적재
    assert "합계" in out
    assert "서울특별시" in out and "경기도" in out
