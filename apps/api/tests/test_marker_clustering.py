"""admin-clustering — 줌-driven 행정 계층(시도→시군구→읍면동→건물). 완전·무편향·라벨 unique·평당가.

줌 밴드가 모드를 정한다(밀도-only 아님): level≥L_SIDO→시도·L_SIGUNGU≤<L_SIDO→시군구·
L_DONG≤<L_SIGUNGU→읍면동·<L_DONG→개별 건물. MARKER_INDIVIDUAL_MAX는 안전망(개별 밴드서 >MAX면
읍면동 폴백). read-only(COUNT+GROUP BY) → 지문/counts 불변. 라벨 unique·구역 합=총(완전·무편향).

★ 회귀: 편향 ORDER BY/LIMIT 없음(부천 non-starved)·시군구 레벨이 동으로 안 쪼개짐(별칭-충돌 가드).
"""

from __future__ import annotations

import sqlite3

from app.search.repo import L_DONG, L_SIDO, L_SIGUNGU, search_marker_feed
from app.search.spec import HardFilterSpec
from app.store.db import _backfill_dong, _backfill_region, get_connection, init_db

# 강남(저 A-id)·부천(고 ro:/of:-id) 두 구역을 가진 bbox.
WIDE = {"min_lat": 37.40, "max_lat": 37.56, "min_lng": 126.74, "max_lng": 127.10}
# 밴드별 테스트 줌 level(임계: L_SIDO=10·L_SIGUNGU=8·L_DONG=5).
LVL_SIDO = L_SIDO + 1       # 시도 밴드
LVL_SIGUNGU = L_SIGUNGU     # 시군구 밴드
LVL_DONG = L_DONG + 1       # 읍면동 밴드(6)
LVL_BLDG = L_DONG - 2       # 개별 건물 밴드(3)


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


# ── 마커 rent 필드(개별 밴드) ──
def test_marker_carries_rent_fields_for_monthly() -> None:
    conn = _conn()
    _insert(conn, [("A0", "A0", "apartment", 37.50, 127.05)])
    conn.execute(
        "INSERT INTO rent_transaction (txn_id, complex_id, net_area, deposit, monthly_rent, "
        "rent_type, deal_date) VALUES ('r0','A0', 84.9, 30000, 100, 'monthly', '2026-05-01')"
    )
    conn.commit()
    feed = search_marker_feed(conn, _spec(deal_type="monthly", level=LVL_BLDG), individual_max=10)
    assert feed.mode == "markers"
    m = next(x for x in feed.markers if x.complex_id == "A0")
    assert m.rent_type == "monthly" and m.deposit == 30000 and m.monthly_rent == 100
    assert m.price == 30000  # 가격축=보증금(전월세)


def test_marker_no_rent_fields_for_sale() -> None:
    conn = _conn()
    _insert(conn, [("A0", "A0", "apartment", 37.50, 127.05)])
    conn.execute(
        'INSERT INTO "transaction" (txn_id, complex_id, net_area, price, deal_date) '
        "VALUES ('t0','A0', 84.9, 142000, '2026-05-01')"
    )
    conn.commit()
    feed = search_marker_feed(conn, _spec(level=LVL_BLDG), individual_max=10)
    m = next(x for x in feed.markers if x.complex_id == "A0")
    assert m.price == 142000 and m.rent_type is None and m.deposit is None


# ── 줌-driven 모드 + 개별 밴드 안전망 ──
def test_building_band_individual_all_returned() -> None:
    # 개별 밴드(level<L_DONG)·≤MAX → mode='markers'·전부(절단 0·grid 병합 0은 클라).
    conn = _conn()
    _insert(conn, [(f"A{i}", f"A{i}", "apartment", 37.50, 127.05 + i * 0.001) for i in range(5)])
    feed = search_marker_feed(conn, _spec(level=LVL_BLDG), individual_max=10)
    assert feed.mode == "markers"
    assert {m.complex_id for m in feed.markers} == {f"A{i}" for i in range(5)}
    assert feed.clusters == []


def test_building_band_safety_net_falls_back_to_dong() -> None:
    # ★ 개별 밴드여도 뷰포트 개별 >MAX면 최하위 행정(읍면동)으로 폴백(수천 마커 방지·안전망).
    conn = _conn()
    rows = [(f"A{i}", f"A{i}", "apartment", 37.50, 127.0 + i * 0.001) for i in range(10)]
    _insert(conn, rows)
    assert search_marker_feed(conn, _spec(level=LVL_BLDG), individual_max=10).mode == "markers"
    fb = search_marker_feed(conn, _spec(level=LVL_BLDG), individual_max=9)
    assert fb.mode == "clusters" and all(c.admin == "dong" for c in fb.clusters)


def test_zoom_drives_clusters_not_density() -> None:
    # ★ 줌-driven: 시군구 밴드면 카운트 적어도(≤MAX) 클러스터(밀도-only 아님).
    conn = _conn()
    rows = [(f"A{i}", 37.50, 127.05, "서울특별시 강남구 테헤란로 1") for i in range(3)]
    _insert(conn, _addr_rows(rows), cols="complex_id, name, property_type, lat, lng, road_addr")
    feed = search_marker_feed(conn, _spec(level=LVL_SIGUNGU), individual_max=999)
    assert feed.mode == "clusters" and feed.clusters[0].admin == "sigungu"


# ── 시도 레벨(신규 최상위) ──
def test_sido_level_clusters() -> None:
    # level≥L_SIDO → 시도 클러스터(라벨=시도명·구역 합=총·admin='sido'·zoom_to=다음 밴드).
    conn = _conn()
    rows = [(f"A{i}", 37.50, 127.05, "서울특별시 강남구 테헤란로 1") for i in range(30)]
    rows += [(f"B{i}", 37.47, 126.82, "경기도 부천시 길주로 1") for i in range(20)]
    _insert(conn, _addr_rows(rows), cols="complex_id, name, property_type, lat, lng, road_addr")
    feed = search_marker_feed(conn, _spec(level=LVL_SIDO), individual_max=5)
    assert feed.mode == "clusters"
    labels = sorted(c.region or "" for c in feed.clusters)
    assert labels == ["경기도", "서울특별시"]  # 시도명·unique
    assert all(c.admin == "sido" and c.zoom_to == L_SIDO - 1 for c in feed.clusters)
    assert sum(c.count for c in feed.clusters) == 50  # 완전(절단 0)


# ── 완전성 + 바운드(시군구) ──
def test_dense_bbox_clusters_complete_and_bounded() -> None:
    conn = _conn()
    rows = [
        (f"A{i}", 37.42 + (i % 30) * 0.004, 126.76 + (i // 30) * 0.01,
         "서울특별시 강남구 테헤란로 1" if i % 2 else "서울특별시 송파구 올림픽로 2")
        for i in range(120)
    ]
    _insert(conn, _addr_rows(rows), cols="complex_id, name, property_type, lat, lng, road_addr")
    feed = search_marker_feed(conn, _spec(level=LVL_SIGUNGU), individual_max=20)
    assert feed.mode == "clusters" and feed.markers == []
    assert sum(c.count for c in feed.clusters) == 120  # 완전 — 절단 0
    for c in feed.clusters:  # 중심 bbox 내
        assert WIDE["min_lat"] <= c.lat <= WIDE["max_lat"]
        assert WIDE["min_lng"] <= c.lng <= WIDE["max_lng"]


# ── ★ 라벨 unique (강남구 ×7 금지·시군구 레벨이 동으로 안 쪼개짐) ──
def test_sigungu_level_one_cluster_per_region() -> None:
    conn = _conn()
    dongs = ["역삼동", "대치동", "삼성동", "청담동", "논현동", "압구정동"]
    rows = []
    for i in range(60):
        rows.append((f"G{i}", f"G{i}", "apartment", 37.45 + (i % 10) * 0.01,
                     126.80 + (i // 10) * 0.04, "서울특별시 강남구 테헤란로 1", dongs[i % 6]))
    _insert(conn, rows, cols="complex_id, name, property_type, lat, lng, road_addr, dong")
    feed = search_marker_feed(conn, _spec(level=LVL_SIGUNGU), individual_max=20)
    assert feed.mode == "clusters"
    labels = [c.region for c in feed.clusters]
    assert labels == ["강남구"]  # 여러 동이어도 단일 구 클러스터(동으로 안 쪼개짐)
    assert sum(c.count for c in feed.clusters) == 60  # 완전


def test_region_labels_unique_per_level() -> None:
    conn = _conn()
    rows = []
    gus = ["강남구", "송파구", "서초구"]
    for i in range(90):
        gu = gus[i % 3]
        rows.append((f"C{i}", 37.45 + (i % 9) * 0.01, 126.80 + (i // 30) * 0.05,
                     f"서울특별시 {gu} 무슨로 {i}"))
    _insert(conn, _addr_rows(rows), cols="complex_id, name, property_type, lat, lng, road_addr")
    feed = search_marker_feed(conn, _spec(level=LVL_SIGUNGU), individual_max=20)
    labels = [c.region or "" for c in feed.clusters]
    assert sorted(labels) == sorted(gus)  # 구 전부 표현(무편향)
    assert len(labels) == len(set(labels))  # 레벨당 unique(중복 0)
    assert sum(c.count for c in feed.clusters) == 90  # 완전


# ── ★ 회귀: 부천(고-id) non-starved ──
def test_high_id_region_not_starved_in_clusters() -> None:
    conn = _conn()
    gangnam = [(f"A1000{i:03d}", 37.50, 127.05, "서울특별시 강남구 테헤란로 1") for i in range(50)]
    bucheon = [("ro:41210:x", 37.47, 126.82, "경기도 부천시 길주로 1"),
               ("of:41210:y", 37.48, 126.83, "경기도 부천시 길주로 2")]
    _insert(conn, _addr_rows(gangnam + bucheon),
            cols="complex_id, name, property_type, lat, lng, road_addr")
    feed = search_marker_feed(conn, _spec(level=LVL_SIGUNGU), individual_max=10)
    assert feed.mode == "clusters"
    bucheon_cluster = next((c for c in feed.clusters if c.region == "부천시"), None)
    assert bucheon_cluster is not None and bucheon_cluster.count == 2, "부천 굶김(편향 미제거)"
    assert sum(c.count for c in feed.clusters) == 52  # 완전(강남 50 + 부천 2)


# ── 줌별 밴드 선택 (시군구 vs 읍면동) ──
def test_level_selects_sigungu_band() -> None:
    conn = _conn()
    rows = [(f"A{i}", f"A{i}", "apartment", 37.50, 127.05,
             "서울특별시 강남구 역삼동 1", "역삼동") for i in range(30)]
    _insert(conn, rows, cols="complex_id, name, property_type, lat, lng, road_addr, dong")
    feed = search_marker_feed(conn, _spec(level=LVL_SIGUNGU), individual_max=5)
    assert feed.mode == "clusters"
    assert all(c.region == "강남구" and c.admin == "sigungu" for c in feed.clusters)


def test_level_selects_dong_band() -> None:
    # 같은 구·다른 동 → 읍면동 밴드선 동별 클러스터(라벨 "시군구 읍면동"·unique·zoom_to=건물 밴드).
    conn = _conn()
    rows = [(f"Y{i}", f"Y{i}", "apartment", 37.50, 127.04,
             "서울특별시 강남구 역삼동 1", "역삼동") for i in range(20)]
    rows += [(f"D{i}", f"D{i}", "apartment", 37.51, 127.05,
              "서울특별시 강남구 대치동 1", "대치동") for i in range(20)]
    _insert(conn, rows, cols="complex_id, name, property_type, lat, lng, road_addr, dong")
    feed = search_marker_feed(conn, _spec(level=LVL_DONG), individual_max=5)
    assert feed.mode == "clusters"
    labels = sorted(c.region or "" for c in feed.clusters)
    assert labels == ["강남구 대치동", "강남구 역삼동"]  # 동별·"시군구 읍면동" 라벨·unique
    assert all(c.admin == "dong" and c.zoom_to == L_DONG - 1 for c in feed.clusters)
    assert sum(c.count for c in feed.clusters) == 40  # 완전


def test_dong_band_clusters_rural_eupmyeon() -> None:
    # ★ 읍면동이 농촌 읍/면도 정확히 클러스터(dong 컬럼이 extract_dong으로 읍/면 포함). 도시 동만 X.
    conn = _conn()
    rows = [(f"E{i}", f"E{i}", "apartment", 37.50, 127.04,
             "경기도 양평군 양평읍 1", "양평읍") for i in range(15)]
    rows += [(f"M{i}", f"M{i}", "apartment", 37.51, 127.05,
              "경기도 양평군 용문면 1", "용문면") for i in range(15)]
    _insert(conn, rows, cols="complex_id, name, property_type, lat, lng, road_addr, dong")
    feed = search_marker_feed(conn, _spec(level=LVL_DONG), individual_max=5)
    labels = sorted(c.region or "" for c in feed.clusters)
    assert labels == ["양평군 양평읍", "양평군 용문면"]  # 읍·면 정확 클러스터
    assert sum(c.count for c in feed.clusters) == 30  # 완전


# ── hard 필터 두 모드 적용 ──
def test_clusters_respect_hard_filter() -> None:
    conn = _conn()
    rows = [(f"A{i}", f"A{i}", "apartment", 37.45, 126.80 + i * 0.001,
             "서울특별시 강남구 x", 1.5 if i % 2 == 0 else 0.5) for i in range(40)]
    _insert(conn, rows,
            cols="complex_id, name, property_type, lat, lng, road_addr, parking_ratio")
    feed = search_marker_feed(
        conn, _spec(parking_ratio_gte=1.0, level=LVL_SIGUNGU), individual_max=5)
    assert feed.mode == "clusters"
    assert sum(c.count for c in feed.clusters) == 20  # ratio>=1.0인 20개만(필터 반영)


# ── 동 백필 = extract_dong 일치(읍/면 포함) ──
def test_dong_backfill_matches_extract_dong() -> None:
    from app.match.normalize import extract_dong

    conn = _conn()
    conn.execute(
        "INSERT INTO complex (complex_id, name, property_type, lat, lng, legal_addr) "
        "VALUES ('A', 'A', 'apartment', 37.5, 127.05, '서울특별시 강남구 역삼동 711-1 역삼자이')"
    )
    conn.execute(
        "INSERT INTO complex (complex_id, name, property_type, lat, lng, road_addr) "
        "VALUES ('B', 'B', 'apartment', 37.5, 127.05, '경기도 부천시 중동 1140 위브더스테이트')"
    )
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
    assert dc is None
    assert _backfill_dong(conn) == 0


# ── 구역 평균 평당가 (색용) ──
def test_cluster_ppp_is_region_representative_average() -> None:
    from app.search.repo import SQM_PER_PYEONG

    conn = _conn()
    rows = [("G0", "G0", "apartment", 37.50, 127.05, "서울특별시 강남구 x"),
            ("G1", "G1", "apartment", 37.50, 127.05, "서울특별시 강남구 y")]
    rows += [(f"P{i}", f"P{i}", "apartment", 37.50, 127.05, "서울특별시 강남구 z")
             for i in range(10)]
    _insert(conn, rows, cols="complex_id, name, property_type, lat, lng, road_addr")
    conn.executemany(
        'INSERT INTO "transaction" (txn_id, complex_id, net_area, price, deal_date) '
        "VALUES (?, ?, ?, ?, ?)",
        [("t0", "G0", 100.0, 100000, "2026-01-01"),
         ("t1", "G1", 100.0, 200000, "2026-02-01")],
    )
    conn.commit()
    feed = search_marker_feed(conn, _spec(level=LVL_SIGUNGU), individual_max=5)
    assert feed.mode == "clusters"
    cl = next(c for c in feed.clusters if c.region == "강남구")
    expected = ((100000 * SQM_PER_PYEONG / 100) + (200000 * SQM_PER_PYEONG / 100)) / 2
    assert cl.ppp is not None and abs(cl.ppp - expected) < 1e-6
    assert cl.count == 12  # 카운트엔 거래없는 패딩도 포함(완전)


# ── region-normalize(#6-②): sido 백필 + bjd authoritative sigungu 교정 ──
def test_backfill_region_sido_and_sigungu() -> None:
    conn = _conn()
    cols = ("complex_id, name, sido, sigungu, bjd_code, road_addr, legal_addr, "
            "property_type, lat, lng")
    conn.executemany(
        f"INSERT INTO complex ({cols}) VALUES (?,?,?,?,?,?,?,?,?,?)",
        [
            # (a) bjd-present·sido 빈·sigungu bare("용인시") → sido 채움+sigungu 일반구 교정.
            ("C1", "c1", None, "용인시", "4146100000", "",
             "경기도 용인시 처인구 ...", "apartment", 37.2, 127.2),
            # (b) bjd-present 통합시 일반구 머지형 — CSV canonical이라 sigungu 불변.
            ("C2", "c2", None, "안산상록구", "4127100000", "",
             "경기도 안산상록구 일동 ...", "apartment", 37.3, 126.8),
            # (c) bjd-absent·sido 빈·주소 변종("강원") → canonical_sido 매핑.
            ("C3", "c3", None, "춘천시", None, "", "강원 춘천시 ...", "rowhouse", 37.8, 127.7),
            # (d) bjd-absent·주소 없음 → sido 소스 없음 → NULL 유지.
            ("C4", "c4", None, None, None, "", "", "officetel", 37.5, 127.0),
        ],
    )
    conn.commit()
    res = _backfill_region(conn)
    got = {
        r["complex_id"]: (r["sido"], r["sigungu"])
        for r in conn.execute("SELECT complex_id, sido, sigungu FROM complex")
    }
    assert got["C1"] == ("경기도", "용인처인구")  # bjd authoritative: bare 시 → 일반구
    assert got["C2"] == ("경기도", "안산상록구")  # 머지형 = CSV canonical → 불변(임의 시삽입 X)
    assert got["C3"][0] == "강원특별자치도"  # 변종 정규화
    assert got["C4"] == (None, None)  # 소스 없음 → NULL 유지
    assert res["sido_filled"] == 3 and res["sigungu_fixed"] == 1
    # 멱등: 재실행 0/0.
    assert _backfill_region(conn) == {"sido_filled": 0, "sigungu_fixed": 0}
