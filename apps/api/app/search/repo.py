"""search_complexes — HardFilterSpec → complex ⨝ transaction → 후보(이진 in/out).

설계 §7: hard 조건은 이진 in/out. soft 점수·랭킹 없음(중립 정렬: 대표거래 최근일 desc).
저신뢰 매칭은 제외하지 않고 match_confidence를 후보에 실어 "추정 매칭" 배지를 가능케 한다(§5.1).
gym은 어디에도 없다(R1 — Tier-2 소관).
"""

from __future__ import annotations

import sqlite3
from typing import Literal

from pydantic import BaseModel

from app.poi.store import PoiNear
from app.school.assignment import AssignmentRow
from app.school.store import SchoolNear
from app.search.criteria import CriterionEval
from app.search.floorplan import FloorplanSummary
from app.search.gym import GymSummary
from app.search.pet import PetSummary
from app.search.review import ReviewSummary
from app.search.spec import HardFilterSpec


class RepresentativeTrade(BaseModel):
    """범위 내 최근 1건 — 카드의 실거래 줄. deal_type별 가격축만 채워짐(나머지 None)."""

    net_area: float | None
    price: int | None  # 만원 (매매)
    deposit: int | None = None  # 만원 (전세·월세 보증금)
    monthly_rent: int | None = None  # 만원 (월세)
    rent_type: str | None = None  # 'jeonse' | 'monthly' (전월세만)
    floor: int | None
    deal_date: str | None  # ISO
    match_confidence: float | None  # 추정매칭 배지용 (저신뢰면 낮음 / NULL=직접확인불가)


class AreaBucket(BaseModel):
    """한 단지의 평형(전용면적 버킷) 집계 — 디테일 카드 다평형 브레이크다운(detail-1).

    범위 내 실거래를 net_area로 single-linkage 클러스터(decimal 노이즈 흡수, 과분할 금지)한
    버킷별 요약. 대표 net_area=버킷 최다거래 면적(tie→최근). 금액축은 deal_type별
    (매매=price / 전월세=deposit, 만원). **읽기전용 집계** — 거래를 만들지 않는다(있는 만큼 정직).
    """

    net_area: float | None  # 대표 전용(㎡) — 프론트가 단위(평/㎡)로 포맷
    transaction_count: int
    recent_amount: int | None  # 만원 — 최근 거래의 가격축(매매=price / 전월세=deposit)
    recent_monthly_rent: int | None = None  # 만원 — 월세 최근 거래만
    recent_rent_type: str | None = None  # 'jeonse' | 'monthly' (전월세)
    recent_deal_date: str | None  # ISO — 버킷 내 최근 거래일
    amount_min: int | None  # 버킷 가격대(금액축 min~max)
    amount_max: int | None


class Candidate(BaseModel):
    """후보 단지 — 필터된 complex 속성 + 거래 요약. 카드 ✓ 렌더용."""

    complex_id: str
    name: str | None
    approval_date: str | None
    parking_ratio: float | None
    parking_underground: int | None
    household_count: int | None
    lat: float | None
    lng: float | None
    source_url: str | None
    transaction_count: int
    price_min: int | None
    price_max: int | None
    representative_trade: RepresentativeTrade | None
    # detail-1: 평형(전용면적)별 집계 — 다평형 건물 카드 브레이크다운. 단일평형이면 길이 1.
    area_buckets: list[AreaBucket] | None = None
    # P4-2a: 구조화 soft/hard 조건 평가용 필드(P4-1 적재분). repo가 SELECT해 채운다.
    subway_time: str | None = None
    has_daycare: bool | None = None
    elevator_count: int | None = None
    cctv_count: int | None = None
    top_floor: int | None = None
    heat_type: str | None = None
    builder: str | None = None
    # Tier-2 soft(R1: hard filter 아님 — 후보 산출 후 attach_*로 부착). repo는 안 채움.
    gym: GymSummary | None = None
    pet: PetSummary | None = None
    review: ReviewSummary | None = None  # 후기(표시 전용 — 랭킹 신호 아님, P3-1)
    floorplan: FloorplanSummary | None = None  # 평면도 feature(표시 전용 — 랭킹 아님, P3-2)
    # poi-1: 정적 POI 근접(eager Tier-1). 카드 표시 + subway/mart hard 필터. attach_poi가 채움.
    poi: list[PoiNear] | None = None
    # school-1: 학교 거리 근접(eager Tier-1). 카드 + 초/중/고 거리 hard 필터. attach_school 채움.
    school: list[SchoolNear] | None = None
    # school-2: 배정 초등 통학구역(advisory). attach_assignment 채움. 미배정=빈 리스트(dash).
    assignment: list[AssignmentRow] | None = None
    # P4-2a: 활성 soft 조건별 평가(설계 §7 ✓/△/✗ + 프론트 튜닝 재료). ranking이 채운다.
    criteria_eval: list[CriterionEval] | None = None


class MarkerCandidate(BaseModel):
    """지도 마커 전용 경량 레코드 — 뷰포트 내 *전체* 단지(랭킹·criteria_eval·enrichment 없음).

    라벨/평당가 계산에 필요한 최소 필드만. price=대표 금액(매매=price / 전월세=deposit). P4-3a-2.
    """

    complex_id: str
    name: str | None
    lat: float | None
    lng: float | None
    price: int | None  # 만원 — 대표 거래 금액(거래유형별)
    net_area: float | None  # 전용(㎡)


# 마커 피드 서버 캡(degenerate 무-bbox 안전망 — 정상 경로는 COUNT 스위치가 처리).
MARKER_CAP = 2500
# server-marker-clustering: COUNT 스위치 — 매칭 ≤MAX면 전부 개별 마커(절단 0), 초과면 행정구역 집계.
MARKER_INDIVIDUAL_MAX = 1200  # 이하 = 개별(완전·편향 ORDER BY 컷 없음)
# region-clustering: 줌 레벨 → 행정단위. Kakao map level은 클수록 줌아웃(넓음). 이 임계 이상이면
# 시군구(구) 집계, 미만이면 동 집계. COUNT 스위치가 줌인 시 개별로 떨구므로 동 집계는 중밀도 구간만.
# 라이브 튜닝 대상(라벨 입자도). level 미지정(None)이면 가장 거친 시군구로(안전 기본).
REGION_SIGUNGU_MIN_LEVEL = 7

# 평당가(만원/평) = 금액(만원) / (전용㎡ / SQM_PER_PYEONG). 프론트 SQM_PER_PYEONG와 동일 상수.
SQM_PER_PYEONG = 3.3058

# 지역명(시군구) 라벨 — initial-load-perf: init_db가 백필한 complex.sigungu 컬럼 우선(핫 쿼리 가속),
# 비어있으면 road_addr(없으면 legal_addr) 2번째 토큰 파싱 폴백("서울 강남구 …"→"강남구"·라벨 동일).
# COALESCE라 백필 후엔 컬럼 시크(파싱 생략·~30% 빠름)·미백필 DB도 정확. 끝토큰 방어로 ||' '.
_ADDR = "COALESCE(NULLIF(c.road_addr, ''), c.legal_addr, '')"
_SIGUNGU_PARSE = (
    f"substr(substr({_ADDR}, instr({_ADDR}, ' ') + 1), 1, "
    f"instr(substr({_ADDR}, instr({_ADDR}, ' ') + 1) || ' ', ' ') - 1)"
)
_SIGUNGU = f"COALESCE(NULLIF(c.sigungu, ''), {_SIGUNGU_PARSE})"
# region-clustering: 동 — init_db가 extract_dong으로 백필한 complex.dong 컬럼(99%+). 빈 행은 ''로
# 두어 시군구로 폴백(완전성 보존). SQL 동 파싱은 정규식이라 컬럼 의존(백필이 라벨 정확성 담보).
_DONG = "COALESCE(NULLIF(c.dong, ''), '')"


class Cluster(BaseModel):
    """서버 행정구역 클러스터 — 구역 중심(AVG) + 카운트 + 지역명(구 or 동) + 평균 평당가(색용)."""

    lat: float
    lng: float
    count: int
    region: str | None = None  # 구역명(시군구 or "시군구 동") — 라벨. 없으면 카운트만.
    ppp: float | None = None  # 구역 대표거래 평균 평당가(만원/평) — tier 색용. 거래 0이면 None.


class MarkerFeed(BaseModel):
    """마커 피드 — 서버가 밀도로 모드 결정. markers(개별·≤MAX) 또는 clusters(행정구역 집계).

    한 모드의 리스트만 채운다. clusters는 편향 `ORDER BY complex_id LIMIT` 절단을 대체 —
    뷰포트 전 구역(부천 포함) 무편향·완전 표현. read-only(COUNT+GROUP BY) → 지문/counts 불변.
    """

    mode: Literal["markers", "clusters"]
    markers: list[MarkerCandidate] = []
    clusters: list[Cluster] = []


def _resolve_assigned(conn: sqlite3.Connection, spec: HardFilterSpec) -> list[str] | None:
    """spec.assigned_school → 매칭 stored school_name 리스트(fuzzy). 미지정이면 None(쿼리 0).

    DISTINCT school_name(~5k) read만 → 지문/counts 불변. 결과를 _complex_where에 IN 절로 넘긴다.
    """
    if spec.assigned_school is None:
        return None
    from app.school.assignment import resolve_assigned_schools

    return resolve_assigned_schools(conn, spec.assigned_school)


def _complex_where(
    spec: HardFilterSpec, *, assigned_schools: list[str] | None = None
) -> tuple[list[str], list[object]]:
    clauses: list[str] = []
    params: list[object] = []
    if spec.approval_year_min is not None:
        clauses.append("CAST(substr(c.approval_date, 1, 4) AS INTEGER) >= ?")
        params.append(spec.approval_year_min)
    if spec.approval_year_max is not None:
        clauses.append("CAST(substr(c.approval_date, 1, 4) AS INTEGER) <= ?")
        params.append(spec.approval_year_max)
    if spec.parking_ratio_gte is not None:
        clauses.append("c.parking_ratio >= ?")
        params.append(spec.parking_ratio_gte)
    if spec.parking_underground:  # True일 때만 — 지하주차 보유 요구
        clauses.append("c.parking_underground > 0")
    if spec.household_count_min is not None:
        clauses.append("c.household_count >= ?")
        params.append(spec.household_count_min)
    if spec.household_count_max is not None:
        clauses.append("c.household_count <= ?")
        params.append(spec.household_count_max)
    # P4-2a: 구조화 필드 hard 연결(준 것만 — in/out).
    if spec.subway_walkable:  # 역세권 — 도보 가까운 카테고리만
        clauses.append("c.subway_time IN ('5분이내', '5~10분이내')")
    if spec.has_daycare:
        clauses.append("c.has_daycare = 1")
    if spec.elevator_count_min is not None:
        clauses.append("c.elevator_count >= ?")
        params.append(spec.elevator_count_min)
    if spec.cctv_count_min is not None:
        clauses.append("c.cctv_count >= ?")
        params.append(spec.cctv_count_min)
    if spec.top_floor_min is not None:
        clauses.append("c.top_floor >= ?")
        params.append(spec.top_floor_min)
    if spec.heat_type is not None:
        clauses.append("c.heat_type = ?")
        params.append(spec.heat_type)
    if spec.builder is not None:
        clauses.append("c.builder LIKE ?")
        params.append(f"%{spec.builder}%")
    if spec.property_type is not None:  # P5-1: 주택유형 필터. 안 주면 전 유형(아파트+비아파트).
        if spec.property_type == "apartment":
            # 레거시 NULL(백필 전 K-apt 행)도 아파트로 취급 — 백필 유무와 무관하게 정합.
            clauses.append("(c.property_type = 'apartment' OR c.property_type IS NULL)")
        else:
            clauses.append("c.property_type = ?")
            params.append(spec.property_type)
    if spec.has_bbox:
        clauses.append("c.lat IS NOT NULL AND c.lng IS NOT NULL")
        clauses.append("c.lat BETWEEN ? AND ? AND c.lng BETWEEN ? AND ?")
        params += [spec.min_lat, spec.max_lat, spec.min_lng, spec.max_lng]
    # poi-1: 정적 POI 근접 hard 필터. ⚠ **미적재=KEEP** — 해당 카테고리 행이 없으면(아직 배치
    # 안 돈 단지) 거르지 않는다(없는 데이터로 제외 금지). present-and-failing(행 있고 미달)만 제외.
    if spec.subway_max_dist_m is not None:
        clauses.append(_poi_keep_or(  # SW8 nearest ≤ N (NULL=반경내 0건 → 미달로 제외)
            "SW8", "p.nearest_dist_m IS NOT NULL AND p.nearest_dist_m <= ?"
        ))
        params.append(spec.subway_max_dist_m)
    if spec.mart_count_1km_min is not None:
        clauses.append(_poi_keep_or("MT1", "p.count_1km >= ?"))  # 1km내 마트 N개
        params.append(spec.mart_count_1km_min)
    # search-deepen-1: POI 풀세트 hard 필터(subway/mart 미러). ⚠ **미적재=KEEP**(동형).
    if spec.conv_count_1km_min is not None:
        clauses.append(_poi_keep_or("CS2", "p.count_1km >= ?"))  # 1km내 편의점 N개
        params.append(spec.conv_count_1km_min)
    for category, dist in (
        ("HP8", spec.hospital_max_dist_m),
        ("PM9", spec.pharmacy_max_dist_m),
        ("PARK", spec.park_max_dist_m),
    ):
        if dist is not None:
            clauses.append(_poi_keep_or(  # 최근접 ≤ N (NULL=반경내 0건 → 미달로 제외)
                category, "p.nearest_dist_m IS NOT NULL AND p.nearest_dist_m <= ?"
            ))
            params.append(dist)
    # school-1: 학교 거리 hard 필터(school_proximity). ⚠ **미적재=KEEP**(poi와 동형).
    for level, dist in (
        ("elem", spec.elem_max_dist_m),
        ("mid", spec.mid_max_dist_m),
        ("high", spec.high_max_dist_m),
    ):
        if dist is not None:
            clauses.append(_school_keep_or(  # 최근접 ≤ N (NULL=해당 level 학교 0개 → 미달로 제외)
                level, "s.nearest_dist_m IS NOT NULL AND s.nearest_dist_m <= ?"
            ))
            params.append(dist)
    # school-assignment: 특정 초등 배정 positive-match. ⚠ **missing≠keep**(거리 필터와 반대) —
    # 이 학교 통학구역만(공동은 여럿 중 하나 매치). 다른학교·무배정·미계산=제외(EXISTS).
    if spec.assigned_school is not None:
        names = assigned_schools or []
        if names:
            ph = ",".join("?" * len(names))
            clauses.append(  # 배정 행(real zone)에 매칭 학교명이 EXISTS — positive-selection
                f"EXISTS (SELECT 1 FROM school_assignment sa WHERE sa.complex_id = c.complex_id "
                f"AND sa.zone_id != '' AND sa.school_name IN ({ph}))"
            )
            params.extend(names)
        else:
            clauses.append("1 = 0")  # 해석된 학교 0 → 무결과(없는 통학구역=제외가 의도)
    return clauses, params


def _school_keep_or(level: str, pass_cond: str) -> str:
    """학교 거리 hard 절 — **미적재=KEEP**: level 행 없으면 통과, 있으면 pass_cond 충족분만.

    correlated (NOT EXISTS level행) OR (EXISTS level행 AND 조건). level은 우리 상수(elem/mid/high).
    """
    return (
        f"(NOT EXISTS (SELECT 1 FROM school_proximity s "
        f"WHERE s.complex_id = c.complex_id AND s.level = '{level}') "
        f"OR EXISTS (SELECT 1 FROM school_proximity s "
        f"WHERE s.complex_id = c.complex_id AND s.level = '{level}' AND {pass_cond}))"
    )


def _poi_keep_or(category: str, pass_cond: str) -> str:
    """POI hard 절 — **미적재=KEEP**: 카테고리 행 없으면 통과, 있으면 pass_cond 충족분만.

    correlated (NOT EXISTS 카테고리행) OR (EXISTS 카테고리행 AND 조건). category는 우리 상수.
    """
    return (
        f"(NOT EXISTS (SELECT 1 FROM poi_proximity p "
        f"WHERE p.complex_id = c.complex_id AND p.category = '{category}') "
        f"OR EXISTS (SELECT 1 FROM poi_proximity p "
        f"WHERE p.complex_id = c.complex_id AND p.category = '{category}' AND {pass_cond}))"
    )


# deal_type → 거래 테이블 · select 컬럼. sale은 기존 transaction(회귀 0).
_TRADE_TABLE = {"sale": "transaction", "jeonse": "rent_transaction", "monthly": "rent_transaction"}
_SALE_COLS = "net_area, price, floor, deal_date, match_confidence"
_RENT_COLS = "net_area, deposit, monthly_rent, rent_type, floor, deal_date, match_confidence"


def _txn_where(spec: HardFilterSpec) -> tuple[list[str], list[object]]:
    """deal_type별 거래-where. sale=price / jeonse·monthly=rent_type+deposit(+monthly_rent).

    rent_type 제약은 rep·매칭·EXISTS 모두에 적용되나 SET 자체는 has_txn_filters가 결정
    (가격/면적 필터 없으면 EXISTS 미요구 → 전 단지, sale과 동일 의미).
    """
    clauses: list[str] = []
    params: list[object] = []
    if spec.net_area_min is not None:
        clauses.append("t.net_area >= ?")
        params.append(spec.net_area_min)
    if spec.net_area_max is not None:
        clauses.append("t.net_area <= ?")
        params.append(spec.net_area_max)
    if spec.deal_type == "sale":
        if spec.price_min is not None:
            clauses.append("t.price >= ?")
            params.append(spec.price_min)
        if spec.price_max is not None:
            clauses.append("t.price <= ?")
            params.append(spec.price_max)
    else:
        clauses.append("t.rent_type = ?")  # jeonse | monthly — 전월세 유형 한정
        params.append(spec.deal_type)
        if spec.deposit_min is not None:
            clauses.append("t.deposit >= ?")
            params.append(spec.deposit_min)
        if spec.deposit_max is not None:
            clauses.append("t.deposit <= ?")
            params.append(spec.deposit_max)
        if spec.deal_type == "monthly":
            if spec.monthly_rent_min is not None:
                clauses.append("t.monthly_rent >= ?")
                params.append(spec.monthly_rent_min)
            if spec.monthly_rent_max is not None:
                clauses.append("t.monthly_rent <= ?")
                params.append(spec.monthly_rent_max)
    if spec.deal_since is not None:
        clauses.append("t.deal_date >= ?")
        params.append(spec.deal_since.isoformat())
    return clauses, params


def _matching_trades(
    conn: sqlite3.Connection, spec: HardFilterSpec, complex_id: str,
    twhere: list[str], tparams: list[object],
) -> list[sqlite3.Row]:
    table = _TRADE_TABLE[spec.deal_type]
    cols = _SALE_COLS if spec.deal_type == "sale" else _RENT_COLS
    where = "t.complex_id = ?"
    params: list[object] = [complex_id]
    if twhere:
        where += " AND " + " AND ".join(twhere)
        params += tparams
    return conn.execute(
        f'SELECT {cols} FROM "{table}" t WHERE {where} ORDER BY t.deal_date DESC',
        params,
    ).fetchall()


def _build_rep(spec: HardFilterSpec, row: sqlite3.Row) -> RepresentativeTrade:
    """deal_type별 대표거래 — sale은 price, 전월세는 deposit/monthly_rent/rent_type."""
    if spec.deal_type == "sale":
        return RepresentativeTrade(
            net_area=row["net_area"], price=row["price"], floor=row["floor"],
            deal_date=row["deal_date"], match_confidence=row["match_confidence"],
        )
    return RepresentativeTrade(
        net_area=row["net_area"], price=None, deposit=row["deposit"],
        monthly_rent=row["monthly_rent"], rent_type=row["rent_type"], floor=row["floor"],
        deal_date=row["deal_date"], match_confidence=row["match_confidence"],
    )


def _area_threshold(area: float) -> float:
    """평형 버킷 경계(㎡) — clamp(5%·면적, 1.5, 4.0).

    net_area 분포가 강한 bimodal(decimal 노이즈<0.5㎡ vs 실평형 경계≥3㎡, valley 희소)이라
    scale-aware: 소형(officetel)은 타이트해 구분 보존, 대형은 4.0㎡로 캡해 같은 타입의 A/B 변형
    (예: 84.6/86.6㎡)을 흡수. valley가 넓어 임계는 결과에 둔감(데이터 근거 §detail-1 선행검증).
    """
    return min(4.0, max(1.5, 0.05 * area))


def _cluster_area_buckets(spec: HardFilterSpec, trades: list[sqlite3.Row]) -> list[AreaBucket]:
    """범위 내 거래를 net_area로 single-linkage 클러스터 → 평형별 집계(평형순 정렬).

    인접 거래의 면적차가 _area_threshold를 넘으면 새 버킷. 대표 net_area=버킷 최다거래 면적
    (tie→최근). 금액축은 deal_type별(매매=price / 전월세=deposit). net_area 없는 거래는 제외.
    **읽기전용** — 있는 거래만 묶는다(과분할·조작 금지). 단일평형이면 버킷 1개.
    """
    amount_col = "price" if spec.deal_type == "sale" else "deposit"
    rows = sorted(
        (t for t in trades if t["net_area"] is not None), key=lambda t: t["net_area"]
    )
    groups: list[list[sqlite3.Row]] = []
    current: list[sqlite3.Row] = []
    for row in rows:
        if current and (row["net_area"] - current[-1]["net_area"]) > _area_threshold(
            current[-1]["net_area"]
        ):
            groups.append(current)
            current = []
        current.append(row)
    if current:
        groups.append(current)

    buckets: list[AreaBucket] = []
    for group in groups:
        # 대표 net_area = 버킷 내 최다거래 면적(tie → 최근 거래일이 있는 면적)
        counts: dict[float, int] = {}
        for t in group:
            counts[t["net_area"]] = counts.get(t["net_area"], 0) + 1
        top = max(counts.values())
        rep_area = max(
            (a for a, c in counts.items() if c == top),
            key=lambda a: max((t["deal_date"] or "") for t in group if t["net_area"] == a),
        )
        recent = max(group, key=lambda t: t["deal_date"] or "")
        amounts = [t[amount_col] for t in group if t[amount_col] is not None]
        is_rent = spec.deal_type != "sale"
        buckets.append(
            AreaBucket(
                net_area=rep_area,
                transaction_count=len(group),
                recent_amount=recent[amount_col],
                recent_monthly_rent=recent["monthly_rent"] if is_rent else None,
                recent_rent_type=recent["rent_type"] if is_rent else None,
                recent_deal_date=recent["deal_date"],
                amount_min=min(amounts) if amounts else None,
                amount_max=max(amounts) if amounts else None,
            )
        )
    return buckets


def search_complexes(conn: sqlite3.Connection, spec: HardFilterSpec) -> list[Candidate]:
    """complex 속성+bbox 필터 → (txn 필터시) 매칭거래 EXISTS → 후보. 이진 in/out, limit."""
    schools = _resolve_assigned(conn, spec)
    cwhere, cparams = _complex_where(spec, assigned_schools=schools)
    twhere, tparams = _txn_where(spec)

    trade_table = _TRADE_TABLE[spec.deal_type]
    # 가격축 집계 컬럼: 매매=price / 전월세=deposit. price_min/max에 deal_type 가격을 싣는다.
    amount_col = "price" if spec.deal_type == "sale" else "deposit"
    # ★ initial-load-perf: **N+1 전에 SQL서 정렬+LIMIT**(구: 전 매칭 후보(7만+)마다 trades 룩업 후
    # 파이썬 sort+slice → 3s). 대표거래 최근일(MAX deal_date·동일 txn 필터) DESC로 정렬해 상위
    # spec.limit만 fetch → trades N+1을 50건으로 한정. **거동 보존**: 같은 50후보(최근일순)·같은
    # 데이터(rep/price/area). idx_txn_complex_date(복합) 활용. NULL(거래 없음)은 DESC서 뒤로(불변).
    txn_cond = (" AND " + " AND ".join(twhere)) if (spec.has_txn_filters and twhere) else ""
    recent_sub = (
        f'(SELECT MAX(t.deal_date) FROM "{trade_table}" t '
        f"WHERE t.complex_id = c.complex_id{txn_cond})"
    )
    sql = (
        "SELECT c.complex_id, c.name, c.approval_date, c.parking_ratio, c.parking_underground, "
        "c.household_count, c.lat, c.lng, c.source_url, "
        "c.subway_time, c.has_daycare, c.elevator_count, c.cctv_count, c.top_floor, "
        f"c.heat_type, c.builder, {recent_sub} AS _recent FROM complex c"
    )
    select_params = list(tparams) if txn_cond else []  # recent_sub는 SELECT라 WHERE보다 먼저 바인드
    parts = list(cwhere)
    where_params = list(cparams)
    if spec.has_txn_filters:
        parts.append(
            f'EXISTS (SELECT 1 FROM "{trade_table}" t '
            f"WHERE t.complex_id = c.complex_id{txn_cond})"
        )
        where_params += tparams
    if parts:
        sql += " WHERE " + " AND ".join(parts)
    sql += " ORDER BY _recent DESC, c.complex_id LIMIT ?"  # 최근일순(중립)·거래없음 뒤로·N+1 한정
    params = [*select_params, *where_params, spec.limit]

    candidates: list[Candidate] = []
    for row in conn.execute(sql, params).fetchall():
        trades = _matching_trades(conn, spec, row["complex_id"], twhere, tparams)
        rep = trades[0] if trades else None
        amounts = [t[amount_col] for t in trades if t[amount_col] is not None]
        candidates.append(
            Candidate(
                complex_id=row["complex_id"],
                name=row["name"],
                approval_date=row["approval_date"],
                parking_ratio=row["parking_ratio"],
                parking_underground=row["parking_underground"],
                household_count=row["household_count"],
                lat=row["lat"],
                lng=row["lng"],
                source_url=row["source_url"],
                transaction_count=len(trades),
                price_min=min(amounts) if amounts else None,
                price_max=max(amounts) if amounts else None,
                representative_trade=_build_rep(spec, rep) if rep is not None else None,
                area_buckets=_cluster_area_buckets(spec, trades),
                subway_time=row["subway_time"],
                has_daycare=None if row["has_daycare"] is None else bool(row["has_daycare"]),
                elevator_count=row["elevator_count"],
                cctv_count=row["cctv_count"],
                top_floor=row["top_floor"],
                heat_type=row["heat_type"],
                builder=row["builder"],
            )
        )
    # 정렬·LIMIT은 SQL서 완료(ORDER BY _recent DESC, complex_id LIMIT) — 파이썬 재정렬 불요.
    return candidates


def _marker_where(
    conn: sqlite3.Connection, spec: HardFilterSpec
) -> tuple[list[str], list[object]]:
    """마커 공통 WHERE — bbox+hard+좌표보유+거래필터 EXISTS. 개별/COUNT/grid 경로 공유.

    search_complexes와 **동일 hard 필터** 재사용(랭킹·soft 없음). 좌표 없는 단지 제외(마커 필수).
    """
    cwhere, cparams = _complex_where(spec, assigned_schools=_resolve_assigned(conn, spec))
    parts = list(cwhere)
    params = list(cparams)
    parts.append("c.lat IS NOT NULL AND c.lng IS NOT NULL")  # 마커는 좌표 필수
    twhere, tparams = _txn_where(spec)
    if spec.has_txn_filters:
        parts.append(
            f'EXISTS (SELECT 1 FROM "{_TRADE_TABLE[spec.deal_type]}" t '
            f"WHERE t.complex_id = c.complex_id AND {' AND '.join(twhere)})"
        )
        params += tparams
    return parts, params


def _fetch_markers(
    conn: sqlite3.Connection,
    spec: HardFilterSpec,
    parts: list[str],
    params: list[object],
    *,
    limit: int | None,
) -> list[MarkerCandidate]:
    """개별 마커 SELECT(+per-row 대표거래 price). limit=None이면 전부(threshold-바운드 경로)."""
    twhere, tparams = _txn_where(spec)
    amount_col = "price" if spec.deal_type == "sale" else "deposit"
    sql = (
        "SELECT c.complex_id, c.name, c.lat, c.lng FROM complex c WHERE "
        + " AND ".join(parts)
        + " ORDER BY c.complex_id"  # 결정론 정렬(절단은 limit이 줄 때만 — 정상 경로는 절단 0)
    )
    p = list(params)
    if limit is not None:
        sql += " LIMIT ?"
        p.append(limit)
    markers: list[MarkerCandidate] = []
    for row in conn.execute(sql, p).fetchall():
        trades = _matching_trades(conn, spec, row["complex_id"], twhere, tparams)
        rep = trades[0] if trades else None
        markers.append(
            MarkerCandidate(
                complex_id=row["complex_id"], name=row["name"],
                lat=row["lat"], lng=row["lng"],
                price=(rep[amount_col] if rep is not None else None),
                net_area=(rep["net_area"] if rep is not None else None),
            )
        )
    return markers


def _admin_level(spec: HardFilterSpec) -> Literal["sigungu", "dong"]:
    """줌 레벨 → 행정단위. 레벨 미지정이거나 충분히 줌아웃(≥임계)이면 시군구(구), 아니면 동."""
    zoomed_in = spec.level is not None and spec.level < REGION_SIGUNGU_MIN_LEVEL
    return "dong" if zoomed_in else "sigungu"


def _ppp_subquery(spec: HardFilterSpec) -> tuple[str, list[object]]:
    """구역 단지의 **대표거래 평당가** 스칼라 서브쿼리(상관). 마커와 동일 거래(최근·동일 txn 필터).

    idx_*_complex_date(복합)로 `complex_id=? … ORDER BY deal_date DESC LIMIT 1`은 인덱스 시크 1건
    → 구역당 N+1 파이썬 루프 없이 단일 GROUP BY 쿼리 안에서 AVG(평당가)로 집계(전용㎡>0만).
    """
    table = _TRADE_TABLE[spec.deal_type]
    amount_col = "price" if spec.deal_type == "sale" else "deposit"
    twhere, tparams = _txn_where(spec)
    txn_cond = (" AND " + " AND ".join(twhere)) if twhere else ""
    sub = (
        f"(SELECT (t.{amount_col} * {SQM_PER_PYEONG}) / t.net_area "
        f'FROM "{table}" t WHERE t.complex_id = c.complex_id '
        f"AND t.net_area IS NOT NULL AND t.net_area > 0 AND t.{amount_col} IS NOT NULL{txn_cond} "
        "ORDER BY t.deal_date DESC LIMIT 1)"
    )
    return sub, list(tparams)


def _region_clusters(
    conn: sqlite3.Connection,
    spec: HardFilterSpec,
    where: str,
    params: list[object],
    admin: Literal["sigungu", "dong"],
) -> list[Cluster]:
    """행정구역(구 or 동) GROUP BY → 구역당 {중심(AVG)·카운트·라벨·평균 평당가}. 무편향·완전·unique.

    격자(grid)를 대체: 한 구를 여러 셀로 쪼개던 라벨 중복(강남구 ×7) 제거. GROUP BY가 뷰포트 행을
    빠짐없이 분할 → 셀 합=총(완전)·구역당 1행(라벨 unique)·ORDER BY/LIMIT 없음(부천 non-starved).
    구 레벨=시군구 GROUP, 동 레벨=(시군구,동) GROUP·라벨 "시군구 동"(동명 충돌 방지·unique).
    평당가는 상관 서브쿼리(idx 시크)를 AVG — N+1 없음. region 빈 행도 '' 그룹으로 카운트(완전).
    """
    ppp_sub, ppp_params = _ppp_subquery(spec)
    # ★ 별칭은 컬럼명(c.sigungu·c.dong)과 충돌 않게 rsgg/rdong — SQLite GROUP BY가 별칭 대신 실
    # 컬럼에 바인딩하면 시군구 레벨도 동으로 묶이는 버그(라벨 중복·레벨 무시). rdong은 컬럼 아님.
    if admin == "sigungu":
        group_keys = f"{_SIGUNGU} AS rsgg, '' AS rdong"
    else:
        group_keys = f"{_SIGUNGU} AS rsgg, {_DONG} AS rdong"
    sql = (
        f"SELECT {group_keys}, "
        "AVG(c.lat) AS clat, AVG(c.lng) AS clng, COUNT(*) AS n, "
        f"AVG({ppp_sub}) AS ppp "
        f"FROM complex c WHERE {where} GROUP BY rsgg, rdong"
    )
    p: list[object] = [*ppp_params, *params]  # ppp_sub는 SELECT라 WHERE보다 먼저 바인드
    out: list[Cluster] = []
    for r in conn.execute(sql, p).fetchall():
        sgg = (r["rsgg"] or "").strip()
        dong = (r["rdong"] or "").strip()
        if admin == "dong":
            label = f"{sgg} {dong}".strip() if dong else (sgg or None)
        else:
            label = sgg or None
        out.append(Cluster(
            lat=r["clat"], lng=r["clng"], count=int(r["n"]),
            region=label, ppp=r["ppp"],
        ))
    return out


def search_markers(
    conn: sqlite3.Connection, spec: HardFilterSpec, *, cap: int = MARKER_CAP
) -> list[MarkerCandidate]:
    """개별 마커 리스트(cap 절단) — 레거시/내부. 무편향 피드는 search_marker_feed."""
    parts, params = _marker_where(conn, spec)
    return _fetch_markers(conn, spec, parts, params, limit=cap)


def search_marker_feed(
    conn: sqlite3.Connection,
    spec: HardFilterSpec,
    *,
    individual_max: int = MARKER_INDIVIDUAL_MAX,
) -> MarkerFeed:
    """★ 무편향 마커 피드 — COUNT 스위치. ≤MAX면 개별 전부(절단 0)·초과면 행정구역 집계(완전).

    편향 `ORDER BY complex_id LIMIT 2500`(부천-굶김 원인) 제거: 밀집 뷰포트는 자르는 대신 행정구역
    (줌별 구/동)으로 집계해 **전 구역 표현·라벨 unique**. hard 필터(bbox+criteria+assigned+txn)는 두
    경로 모두 적용. read-only(COUNT+GROUP BY) → 지문/counts 불변.
    """
    parts, params = _marker_where(conn, spec)
    where = " AND ".join(parts)
    total = conn.execute(f"SELECT COUNT(*) FROM complex c WHERE {where}", params).fetchone()[0]
    if total <= individual_max or not spec.has_bbox:
        # 정상: 개별 전부(threshold 바운드·절단 0). 무-bbox+대량(degenerate)만 안전캡.
        limit = None if total <= individual_max else MARKER_CAP
        markers = _fetch_markers(conn, spec, parts, params, limit=limit)
        return MarkerFeed(mode="markers", markers=markers)
    clusters = _region_clusters(conn, spec, where, params, _admin_level(spec))
    return MarkerFeed(mode="clusters", clusters=clusters)
