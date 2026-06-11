"""region-clustering — COUNT 스위치 + 행정구역(구/동) 집계. 완전·무편향·라벨 unique·평당가. 키리스.

격자(grid)를 행정구역 GROUP BY로 교체: 한 구가 여러 셀로 쪼개지던 라벨 중복(강남구 ×7)을 제거하고
**구역당 1행(unique)**·줌별 구/동·구역 평균 평당가(색용)를 낸다. read-only → 지문/counts 불변.

★ 회귀 보존: 편향 `ORDER BY complex_id LIMIT`이 굶기던 고-id 구역(부천)이 GROUP BY엔 ORDER BY/LIMIT
없어 non-zero(non-starved). 완전성(구역 합=총)·hard 필터 두 모드 적용.
"""

from __future__ import annotations

import sqlite3

from app.search.repo import REGION_SIGUNGU_MIN_LEVEL, search_marker_feed
from app.search.spec import HardFilterSpec
from app.store.db import _backfill_dong, get_connection, init_db

# 강남(저 A-id)·부천(고 ro:/of:-id) 두 구역을 가진 bbox.
WIDE = {"min_lat": 37.40, "max_lat": 37.56, "min_lng": 126.74, "max_lng": 127.10}


def _spec(**kw) -> HardFilterSpec:  # type: ignore[no-untyped-def]
    return HardFilterSpec.model_validate({**WIDE, **kw})


def _conn() -> sqlite3.Connection:
    conn = get_connection(":memory:")
    init_db(conn)
    return conn


def _insert(
    conn: sqlite3.Connection,
    rows: list[tuple],
    *,
    cols: str = "complex_id, name, property_type, lat, lng",
) -> None:
    ph = ", ".join("?" * (cols.count(",") + 1))
    conn.executemany(f"INSERT INTO complex ({cols}) VALUES ({ph})", rows)
    conn.commit()


def _addr_rows(rows: list[tuple[str, float, float, str]]) -> list[tuple]:
    # (cid, lat, lng, road_addr) → INSERT 튜플(property_type=apartment).
    return [(cid, cid, "apartment", lat, lng, ra) for cid, lat, lng, ra in rows]


# ── 모드 스위치 ──
def test_small_bbox_individual_all_returned() -> None:
    # ≤MAX → mode='markers'·전부 반환(절단 0·직접 카운트와 일치).
    conn = _conn()
    _insert(conn, [(f"A{i}", f"A{i}", "apartment", 37.50, 127.05 + i * 0.001) for i in range(5)])
    feed = search_marker_feed(conn, _spec(), individual_max=10)
    assert feed.mode == "markers"
    assert {m.complex_id for m in feed.markers} == {f"A{i}" for i in range(5)}
    assert feed.clusters == []


def test_threshold_boundary() -> None:
    conn = _conn()
    _insert(conn, [(f"A{i}", f"A{i}", "apartment", 37.50, 127.0 + i * 0.001) for i in range(10)])
    assert search_marker_feed(conn, _spec(), individual_max=10).mode == "markers"  # == MAX → 개별
    assert search_marker_feed(conn, _spec(), individual_max=9).mode == "clusters"  # > MAX


# ── 완전성 + 바운드 ──
def test_dense_bbox_clusters_complete_and_bounded() -> None:
    # >MAX → mode='clusters'. 구역 카운트 합 = 총 매칭수(완전성)·중심 bbox 내.
    conn = _conn()
    rows = [
        (f"A{i}", 37.42 + (i % 30) * 0.004, 126.76 + (i // 30) * 0.01,
         "서울특별시 강남구 테헤란로 1" if i % 2 else "서울특별시 송파구 올림픽로 2")
        for i in range(120)
    ]
    _insert(conn, _addr_rows(rows), cols="complex_id, name, property_type, lat, lng, road_addr")
    feed = search_marker_feed(conn, _spec(), individual_max=20)
    assert feed.mode == "clusters" and feed.markers == []
    assert sum(c.count for c in feed.clusters) == 120  # 완전 — 절단 0
    for c in feed.clusters:  # 중심 bbox 내
        assert WIDE["min_lat"] <= c.lat <= WIDE["max_lat"]
        assert WIDE["min_lng"] <= c.lng <= WIDE["max_lng"]


# ── ★ 라벨 unique (강남구 ×7 금지) ──
def test_sigungu_level_one_cluster_per_region() -> None:
    # 한 구를 여러 위치·**여러 동**에 흩어도 시군구 레벨선 **구당 클러스터 1개**(unique·비겹침).
    # ★ 회귀가드: dong 컬럼이 채워져 있어도 구 레벨은 동으로 안 쪼개진다(GROUP BY 별칭-충돌 버그).
    conn = _conn()
    dongs = ["역삼동", "대치동", "삼성동", "청담동", "논현동", "압구정동"]
    rows = []
    for i in range(60):  # 강남구 단지를 여러 동에 흩뿌림(버그면 동별로 쪼개져 ×6)
        rows.append((f"G{i}", f"G{i}", "apartment", 37.45 + (i % 10) * 0.01,
                     126.80 + (i // 10) * 0.04, "서울특별시 강남구 테헤란로 1", dongs[i % 6]))
    _insert(conn, rows, cols="complex_id, name, property_type, lat, lng, road_addr, dong")
    feed = search_marker_feed(conn, _spec(level=10), individual_max=20)
    assert feed.mode == "clusters"
    labels = [c.region for c in feed.clusters]
    assert labels == ["강남구"]  # 여러 동이어도 단일 구 클러스터(동으로 안 쪼개짐)
    assert len(labels) == len(set(labels))  # 중복 0
    assert sum(c.count for c in feed.clusters) == 60  # 완전


def test_region_labels_unique_per_level() -> None:
    # 여러 구가 섞여도 레벨당 라벨 unique(구당 1행). 무편향(모든 구 표현).
    conn = _conn()
    rows = []
    gus = ["강남구", "송파구", "서초구"]
    for i in range(90):
        gu = gus[i % 3]
        rows.append((f"C{i}", 37.45 + (i % 9) * 0.01, 126.80 + (i // 30) * 0.05,
                     f"서울특별시 {gu} 무슨로 {i}"))
    _insert(conn, _addr_rows(rows), cols="complex_id, name, property_type, lat, lng, road_addr")
    feed = search_marker_feed(conn, _spec(level=10), individual_max=20)
    labels = [c.region or "" for c in feed.clusters]
    assert sorted(labels) == sorted(gus)  # 구 전부 표현(무편향)
    assert len(labels) == len(set(labels))  # 레벨당 unique(중복 0)
    assert sum(c.count for c in feed.clusters) == 90  # 완전


# ── ★ 회귀: 부천(고-id) non-starved ──
def test_high_id_region_not_starved_in_clusters() -> None:
    # 강남측 저 A-id 대량 + 부천측 고 ro:/of:-id 소수. 구 ORDER BY complex_id LIMIT면 부천이 잘렸음.
    # GROUP BY엔 ORDER BY/LIMIT 없음 → 부천 구역 클러스터 non-zero(편향 제거 입증).
    conn = _conn()
    gangnam = [(f"A1000{i:03d}", 37.50, 127.05, "서울특별시 강남구 테헤란로 1") for i in range(50)]
    bucheon = [("ro:41210:x", 37.47, 126.82, "경기도 부천시 길주로 1"),
               ("of:41210:y", 37.48, 126.83, "경기도 부천시 길주로 2")]
    _insert(conn, _addr_rows(gangnam + bucheon),
            cols="complex_id, name, property_type, lat, lng, road_addr")
    feed = search_marker_feed(conn, _spec(level=10), individual_max=10)
    assert feed.mode == "clusters"
    bucheon_cluster = next((c for c in feed.clusters if c.region == "부천시"), None)
    assert bucheon_cluster is not None and bucheon_cluster.count == 2, "부천 굶김(편향 미제거)"
    assert sum(c.count for c in feed.clusters) == 52  # 완전(강남 50 + 부천 2)


# ── 줌별 레벨 선택 (구 vs 동) ──
def test_level_selects_sigungu_when_zoomed_out() -> None:
    conn = _conn()
    rows = [(f"A{i}", f"A{i}", "apartment", 37.50, 127.05,
             "서울특별시 강남구 역삼동 1", "역삼동") for i in range(30)]
    _insert(conn, rows, cols="complex_id, name, property_type, lat, lng, road_addr, dong")
    feed = search_marker_feed(conn, _spec(level=REGION_SIGUNGU_MIN_LEVEL), individual_max=5)
    assert feed.mode == "clusters"
    assert all(c.region == "강남구" for c in feed.clusters)  # ≥임계 → 시군구 라벨


def test_level_selects_dong_when_zoomed_in() -> None:
    # 같은 구·다른 동 → 동 레벨선 동별 클러스터(라벨 "시군구 동"·unique·동명 충돌 방지).
    conn = _conn()
    rows = [(f"Y{i}", f"Y{i}", "apartment", 37.50, 127.04,
             "서울특별시 강남구 역삼동 1", "역삼동") for i in range(20)]
    rows += [(f"D{i}", f"D{i}", "apartment", 37.51, 127.05,
              "서울특별시 강남구 대치동 1", "대치동") for i in range(20)]
    _insert(conn, rows, cols="complex_id, name, property_type, lat, lng, road_addr, dong")
    feed = search_marker_feed(conn, _spec(level=REGION_SIGUNGU_MIN_LEVEL - 1), individual_max=5)
    assert feed.mode == "clusters"
    labels = sorted(c.region or "" for c in feed.clusters)
    assert labels == ["강남구 대치동", "강남구 역삼동"]  # 동별·"시군구 동" 라벨·unique
    assert len(labels) == len(set(labels))
    assert sum(c.count for c in feed.clusters) == 40  # 완전


# ── hard 필터 두 모드 적용 ──
def test_clusters_respect_hard_filter() -> None:
    # parking_ratio_gte 필터가 클러스터 카운트도 줄인다(개별과 동일 hard 필터).
    conn = _conn()
    rows = [(f"A{i}", f"A{i}", "apartment", 37.45, 126.80 + i * 0.001,
             "서울특별시 강남구 x", 1.5 if i % 2 == 0 else 0.5) for i in range(40)]
    _insert(conn, rows,
            cols="complex_id, name, property_type, lat, lng, road_addr, parking_ratio")
    feed = search_marker_feed(conn, _spec(parking_ratio_gte=1.0, level=10), individual_max=5)
    assert feed.mode == "clusters"
    assert sum(c.count for c in feed.clusters) == 20  # ratio>=1.0인 20개만(필터 반영)


# ── 동 백필 = extract_dong 일치 ──
def test_dong_backfill_matches_extract_dong() -> None:
    from app.match.normalize import extract_dong

    conn = _conn()
    conn.execute(
        "INSERT INTO complex (complex_id, name, property_type, lat, lng, legal_addr) "
        "VALUES ('A', 'A', 'apartment', 37.5, 127.05, '서울특별시 강남구 역삼동 711-1 역삼자이')"
    )
    # legal_addr 없을 때 road_addr 폴백(지번 동 토큰 보유 주소)
    conn.execute(
        "INSERT INTO complex (complex_id, name, property_type, lat, lng, road_addr) "
        "VALUES ('B', 'B', 'apartment', 37.5, 127.05, '경기도 부천시 중동 1140 위브더스테이트')"
    )
    # 동 토큰 없는 도로명만 → extract_dong None(백필 제외 — 라벨 정확성)
    conn.execute(
        "INSERT INTO complex (complex_id, name, property_type, lat, lng, road_addr) "
        "VALUES ('C', 'C', 'apartment', 37.5, 127.05, '경기도 부천시 원미구 길주로 1')"
    )
    conn.commit()
    n = _backfill_dong(conn)
    assert n == 2  # A·B만 채움(C는 동 토큰 없어 제외)
    da = conn.execute("SELECT dong FROM complex WHERE complex_id='A'").fetchone()[0]
    db_ = conn.execute("SELECT dong FROM complex WHERE complex_id='B'").fetchone()[0]
    dc = conn.execute("SELECT dong FROM complex WHERE complex_id='C'").fetchone()[0]
    assert da == extract_dong("서울특별시 강남구 역삼동 711-1 역삼자이") == "역삼동"
    assert db_ == extract_dong("경기도 부천시 중동 1140 위브더스테이트") == "중동"
    assert dc is None  # 동 토큰 없음 → 미채움
    # 멱등 — 재실행은 0행(A·B는 채워졌고 C는 None이라 그대로)
    assert _backfill_dong(conn) == 0


# ── 구역 평균 평당가 (색용) ──
def test_cluster_ppp_is_region_representative_average() -> None:
    # 구역 단지들의 대표거래(최근) 평당가 평균. 거래 없는 단지는 AVG서 제외(카운트엔 포함).
    from app.search.repo import SQM_PER_PYEONG

    conn = _conn()
    # 강남구 두 단지 — 각각 매매 1건(전용 100㎡). 평당가 = price * SQM_PER_PYEONG / 100.
    rows = [("G0", "G0", "apartment", 37.50, 127.05, "서울특별시 강남구 x"),
            ("G1", "G1", "apartment", 37.50, 127.05, "서울특별시 강남구 y")]
    # 대량 패딩(같은 구역) — clusters 모드 진입용(거래 없음 → ppp AVG 미포함, 카운트만).
    rows += [(f"P{i}", f"P{i}", "apartment", 37.50, 127.05, "서울특별시 강남구 z")
             for i in range(10)]
    _insert(conn, rows, cols="complex_id, name, property_type, lat, lng, road_addr")
    conn.executemany(
        'INSERT INTO "transaction" (txn_id, complex_id, net_area, price, deal_date) '
        "VALUES (?, ?, ?, ?, ?)",
        [("t0", "G0", 100.0, 100000, "2026-01-01"),   # ppp = 100000*K/100 = 1000*K
         ("t1", "G1", 100.0, 200000, "2026-02-01")],  # ppp = 2000*K
    )
    conn.commit()
    feed = search_marker_feed(conn, _spec(level=10), individual_max=5)
    assert feed.mode == "clusters"
    cl = next(c for c in feed.clusters if c.region == "강남구")
    expected = ((100000 * SQM_PER_PYEONG / 100) + (200000 * SQM_PER_PYEONG / 100)) / 2
    assert cl.ppp is not None and abs(cl.ppp - expected) < 1e-6  # 두 거래 평균(패딩 제외)
    assert cl.count == 12  # 카운트엔 거래없는 패딩도 포함(완전)
