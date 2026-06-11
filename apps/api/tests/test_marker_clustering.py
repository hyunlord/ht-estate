"""server-marker-clustering — COUNT 스위치 + grid 집계(완전·무편향·바운드). 키리스.

★ 회귀: 편향 `ORDER BY complex_id LIMIT`이 굶기던 고-id 구역(부천=비아파트 ro:/of:)이 grid에선
non-zero(grid는 ORDER BY/LIMIT 없음). read-only(COUNT+GROUP BY) → 지문/counts 불변.
"""

from __future__ import annotations

import sqlite3

from app.search.repo import GRID_N, search_marker_feed
from app.search.spec import HardFilterSpec
from app.store.db import get_connection, init_db

# 강남(저 A-id)·부천(고 ro:/of:-id) 두 클러스터를 가진 bbox.
WIDE = {"min_lat": 37.40, "max_lat": 37.56, "min_lng": 126.74, "max_lng": 127.10}


def _db(rows: list[tuple[str, float, float]]) -> sqlite3.Connection:
    conn = get_connection(":memory:")
    init_db(conn)
    conn.executemany(
        "INSERT INTO complex (complex_id, name, property_type, lat, lng) "
        "VALUES (?, ?, 'apartment', ?, ?)",
        [(cid, cid, lat, lng) for cid, lat, lng in rows],
    )
    conn.commit()
    return conn


def _spec(**kw) -> HardFilterSpec:  # type: ignore[no-untyped-def]
    return HardFilterSpec.model_validate({**WIDE, **kw})


# ── 모드 스위치 ──
def test_small_bbox_individual_all_returned() -> None:
    # ≤MAX → mode='markers'·전부 반환(절단 0·직접 카운트와 일치).
    rows = [(f"A{i}", 37.50, 127.05 + i * 0.001) for i in range(5)]
    conn = _db(rows)
    feed = search_marker_feed(conn, _spec(), individual_max=10, grid_n=4)
    assert feed.mode == "markers"
    assert {m.complex_id for m in feed.markers} == {f"A{i}" for i in range(5)}
    assert feed.clusters == []


def test_dense_bbox_clusters_complete_and_bounded() -> None:
    # >MAX → mode='clusters'. 셀 카운트 합 = 총 매칭수(완전성)·출력 바운드·중심 bbox 내.
    rows = [(f"A{i}", 37.42 + (i % 30) * 0.004, 126.76 + (i // 30) * 0.01) for i in range(120)]
    conn = _db(rows)
    feed = search_marker_feed(conn, _spec(), individual_max=20, grid_n=8)
    assert feed.mode == "clusters" and feed.markers == []
    assert sum(c.count for c in feed.clusters) == 120  # 완전 — 절단 0
    assert len(feed.clusters) <= (8 + 1) ** 2  # 바운드
    for c in feed.clusters:  # 중심 bbox 내
        assert WIDE["min_lat"] <= c.lat <= WIDE["max_lat"]
        assert WIDE["min_lng"] <= c.lng <= WIDE["max_lng"]


# ── ★ 회귀: 부천(고-id) non-starved ──
def test_high_id_region_not_starved_in_clusters() -> None:
    # 강남측 저 A-id 대량 + 부천측 고 ro:/of:-id 소수. 구 ORDER BY complex_id LIMIT면 부천이 잘렸음.
    # grid엔 ORDER BY/LIMIT 없음 → 부천 셀 non-zero(편향 제거 입증).
    gangnam = [(f"A1000{i:03d}", 37.50, 127.05) for i in range(50)]      # 저 id·강남(lng 127.05)
    bucheon = [("ro:41210:x", 37.47, 126.82), ("of:41210:y", 37.48, 126.83)]  # 고 id·부천
    conn = _db(gangnam + bucheon)
    feed = search_marker_feed(conn, _spec(), individual_max=10, grid_n=12)
    assert feed.mode == "clusters"
    # 부천 구역(lng < 126.9)에 카운트 있는 셀이 존재 → 안 굶음.
    bucheon_count = sum(c.count for c in feed.clusters if c.lng < 126.9)
    assert bucheon_count == 2, f"부천 굶김(편향 미제거): {bucheon_count}"
    assert sum(c.count for c in feed.clusters) == 52  # 완전(강남 50 + 부천 2)


# ── hard 필터 두 모드 적용 ──
def test_clusters_respect_hard_filter() -> None:
    # parking_ratio_gte 필터가 클러스터 카운트도 줄인다(개별과 동일 hard 필터).
    conn = get_connection(":memory:")
    init_db(conn)
    conn.executemany(
        "INSERT INTO complex (complex_id, name, property_type, lat, lng, parking_ratio) "
        "VALUES (?, ?, 'apartment', ?, ?, ?)",
        [(f"A{i}", f"A{i}", 37.45, 126.80 + i * 0.001, 1.5 if i % 2 == 0 else 0.5)
         for i in range(40)],
    )
    conn.commit()
    feed = search_marker_feed(conn, _spec(parking_ratio_gte=1.0), individual_max=5, grid_n=6)
    assert feed.mode == "clusters"
    assert sum(c.count for c in feed.clusters) == 20  # ratio>=1.0인 20개만(필터 반영)


# ── 경계: threshold ──
def test_threshold_boundary() -> None:
    rows = [(f"A{i}", 37.50, 127.0 + i * 0.001) for i in range(10)]
    conn = _db(rows)
    assert search_marker_feed(conn, _spec(), individual_max=10).mode == "markers"  # == MAX → 개별
    assert search_marker_feed(conn, _spec(), individual_max=9, grid_n=4).mode == "clusters"  # > MAX


def test_grid_n_default_coarse() -> None:
    assert 6 <= GRID_N <= 14  # cluster-ux-polish: 적고 큰 병합(과밀 아님)


# ── cluster-ux-polish: 지역명(시군구) 라벨 ──
def test_cluster_region_label_from_address() -> None:
    # road_addr 2번째 토큰(시군구)을 셀 지배 지역명으로. 컬럼 백필 0(read-only 파싱).
    conn = get_connection(":memory:")
    init_db(conn)
    conn.executemany(
        "INSERT INTO complex (complex_id, name, property_type, lat, lng, road_addr) "
        "VALUES (?, ?, 'apartment', ?, ?, ?)",
        [(f"A{i}", f"A{i}", 37.50, 127.05, "서울특별시 강남구 테헤란로 1") for i in range(30)],
    )
    conn.commit()
    feed = search_marker_feed(conn, _spec(), individual_max=5, grid_n=4)
    assert feed.mode == "clusters"
    assert all(c.region == "강남구" for c in feed.clusters)  # 지배 시군구
    assert sum(c.count for c in feed.clusters) == 30  # 완전성 불변


def test_cluster_region_dominant_when_mixed() -> None:
    # 한 셀에 두 시군구 섞이면 최빈이 라벨. legal_addr 폴백도 동작.
    conn = get_connection(":memory:")
    init_db(conn)
    rows = [(f"G{i}", 37.50, 127.05, "서울특별시 강남구 x", None) for i in range(20)]
    rows += [(f"S{i}", 37.50, 127.05, None, "서울특별시 송파구 y 1-1") for i in range(8)]
    conn.executemany(
        "INSERT INTO complex (complex_id, name, property_type, lat, lng, road_addr, legal_addr) "
        "VALUES (?, ?, 'apartment', ?, ?, ?, ?)",
        [(cid, cid, lat, lng, ra, la) for cid, lat, lng, ra, la in rows],
    )
    conn.commit()
    feed = search_marker_feed(conn, _spec(), individual_max=5, grid_n=2)
    assert feed.mode == "clusters"
    big = max(feed.clusters, key=lambda c: c.count)
    assert big.region == "강남구"  # 20 > 8 → 최빈
    assert sum(c.count for c in feed.clusters) == 28  # 완전(강남 20 + 송파 8)
