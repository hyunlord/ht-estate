"""HardFilterSpec — 구조화 hard filter 모델 (이진 in/out 입력).

NL→spec 변환(LLM)은 L3/에이전트 소관(별도 티켓). 여기는 **구조화 spec만** 받는다.
gym 필드는 **없다** — K-apt에 헬스장 데이터가 없어(R1 프로브 0/17) Tier-2 enrichment
소관이다. soft 필터(pet·floorplan·gym)·점수 랭킹도 여기 없음.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.search.criteria import REGISTRY

Preference = Literal["required", "preferred", "none"]
# 거래유형 축(P2-2): 매매=transaction(price) / 전세·월세=rent_transaction(deposit[+monthly_rent]).
DealType = Literal["sale", "jeonse", "monthly"]
# 주택유형 축(P5-1): 아파트/연립다세대/오피스텔/단독. 비-아파트 커버리지. 기본=전 유형(필터 안 줌).
PropertyType = Literal["apartment", "rowhouse", "officetel", "detached"]

# Preference → 가중치(랭킹). 일반화 criteria의 weight와 동일 스케일(required=2·preferred=1).
_PREF_WEIGHT: dict[str, float] = {"required": 2.0, "preferred": 1.0, "none": 0.0}


class SoftCriterion(BaseModel):
    """일반화 soft 조건 한 개 — 레지스트리 key + 가중치. weight=0이면 끄기(demote-not-exclude 유지).

    key는 레지스트리의 **soft-able** 조건만(heat_type·builder 등 hard-only는 거부). #2b/#3가 NL/UX
    튜닝(강/보통/약/끄기)을 weight로 매핑한다 — 여기선 weight 자체만 받는다.
    """

    key: str
    weight: float = Field(default=1.0, ge=0.0)

    @field_validator("key")
    @classmethod
    def _known_soft_key(cls, v: str) -> str:
        crit = REGISTRY.get(v)
        if crit is None:
            raise ValueError(f"unknown criterion key: {v}")
        if not crit.soft_able:
            raise ValueError(f"criterion '{v}' is hard-only (not soft-rankable)")
        return v


class SoftSpec(BaseModel):
    """soft 선호 — 랭킹(ORDER)만 바꾸고 후보 SET은 절대 안 바꾼다(설계 §7·demote-not-exclude).

    **일반화**(P4-2a): 고정 {gym,pet} → 임의 조건. 레거시 `gym`/`pet`(Preference, 후방호환) +
    일반화 `criteria`(임의 조건 + weight) 둘 다 지원. 둘 다 활성 조건으로 합쳐 가중합 랭킹.
    none/빈 리스트 → 중립 정렬 유지. demote-not-exclude: 미충족/데이터없음도 제외 0(강등).
    """

    gym: Preference = "none"
    pet: Preference = "none"
    criteria: list[SoftCriterion] = Field(default_factory=list)

    def active_criteria(self) -> list[tuple[str, float]]:
        """활성 (key, weight) — 레거시 gym/pet(Preference→weight) + criteria(weight>0)."""
        active: list[tuple[str, float]] = []
        if self.gym != "none":
            active.append(("gym", _PREF_WEIGHT[self.gym]))
        if self.pet != "none":
            active.append(("pet", _PREF_WEIGHT[self.pet]))
        active.extend((c.key, c.weight) for c in self.criteria if c.weight > 0)
        return active


class HardFilterSpec(BaseModel):
    """결정론 hard 조건(이진 in/out) + soft 선호(랭킹 전용).

    hard 필드만 후보 SET을 결정한다. soft는 ORDER만 — search_complexes는 soft를 무시하고
    랭킹 레이어(ranking.py)만 읽는다. 모든 hard 필드 optional — 준 것만 AND로 좁힌다.
    """

    # complex 속성
    approval_year_min: int | None = None
    approval_year_max: int | None = None
    parking_ratio_gte: float | None = None
    parking_underground: bool | None = None  # True면 지하주차 보유(>0) 요구
    household_count_min: int | None = None
    household_count_max: int | None = None
    # P4-2a: P4-1 구조화 필드 hard 연결(in/out). 준 것만 AND. gym/pet은 enrichment라 여기 없음(R1).
    subway_walkable: bool | None = None  # True면 역세권(subway_time ∈ 5분/5~10분이내) 요구
    has_daycare: bool | None = None  # True면 단지 내 어린이집 보유 요구
    elevator_count_min: int | None = None  # 승강기 최소 대수
    cctv_count_min: int | None = None  # CCTV 최소 대수
    top_floor_min: int | None = None  # 최고층 하한
    heat_type: str | None = None  # 난방방식 정확 일치(예: '지역난방')
    builder: str | None = None  # 건설사 부분 일치(LIKE %..%)
    property_type: PropertyType | None = None  # 주택유형(P5-1). None=전 유형. 정확 일치.
    # poi-1: 정적 POI 근접 hard 필터(poi_proximity). ⚠ **미적재=KEEP**(없는 데이터로 제외 금지 —
    # repo가 correlated NOT EXISTS OR pass로 구현). present-and-failing만 제외.
    subway_max_dist_m: int | None = None  # 역세권: 최근접 지하철역 ≤ N미터(SW8)
    mart_count_1km_min: int | None = None  # 1km 내 대형마트 ≥ N개(MT1 count_1km)
    # search-deepen-1: POI 풀세트 hard 필터(additive·기존 subway/mart 미러). ⚠ 미적재=KEEP(동형).
    conv_count_1km_min: int | None = None  # 1km 내 편의점 ≥ N개(CS2 count_1km)
    hospital_max_dist_m: int | None = None  # 최근접 병원 ≤ N미터(HP8)
    pharmacy_max_dist_m: int | None = None  # 최근접 약국 ≤ N미터(PM9)
    park_max_dist_m: int | None = None  # 최근접 공원 ≤ N미터(PARK)
    # school-1: 학교 거리 근접 hard 필터(school_proximity). ⚠ **미적재=KEEP**(poi와 동일).
    elem_max_dist_m: int | None = None  # 최근접 초등학교 ≤ N미터(주 필터)
    mid_max_dist_m: int | None = None  # 최근접 중학교 ≤ N미터
    high_max_dist_m: int | None = None  # 최근접 고등학교 ≤ N미터

    # 거래유형(P2-2). 기본 sale → 기존 매매 동작 그대로(회귀 0).
    deal_type: DealType = "sale"

    # 거래 속성 (있으면 매칭 거래 EXISTS 요구). 가격축은 deal_type별:
    #   sale=price / jeonse=deposit / monthly=deposit(+monthly_rent). net_area·deal_since 공유.
    net_area_min: float | None = None
    net_area_max: float | None = None
    price_min: int | None = None  # 만원 (매매)
    price_max: int | None = None
    deposit_min: int | None = None  # 만원 (전세·월세 보증금)
    deposit_max: int | None = None
    monthly_rent_min: int | None = None  # 만원 (월세)
    monthly_rent_max: int | None = None
    deal_since: date | None = None

    # 지도 bbox (4개 모두 또는 모두 없음)
    min_lat: float | None = None
    max_lat: float | None = None
    min_lng: float | None = None
    max_lng: float | None = None

    limit: int = Field(default=50, ge=1, le=200)

    # soft 선호 — 랭킹 전용(SET 불변). 기본 all-none → 중립 정렬.
    soft: SoftSpec = Field(default_factory=SoftSpec)

    @model_validator(mode="after")
    def _check_coherence(self) -> HardFilterSpec:
        pairs = [
            (self.approval_year_min, self.approval_year_max, "approval_year"),
            (self.net_area_min, self.net_area_max, "net_area"),
            (self.price_min, self.price_max, "price"),
            (self.deposit_min, self.deposit_max, "deposit"),
            (self.monthly_rent_min, self.monthly_rent_max, "monthly_rent"),
            (self.household_count_min, self.household_count_max, "household_count"),
        ]
        for lo, hi, name in pairs:
            if lo is not None and hi is not None and lo > hi:
                raise ValueError(f"{name}: min({lo}) > max({hi})")

        bbox = (self.min_lat, self.max_lat, self.min_lng, self.max_lng)
        if any(v is not None for v in bbox) and any(v is None for v in bbox):
            raise ValueError("bbox는 min_lat·max_lat·min_lng·max_lng 4개 모두 필요")
        if self.min_lat is not None and self.max_lat is not None and self.min_lat > self.max_lat:
            raise ValueError(f"bbox lat: min({self.min_lat}) > max({self.max_lat})")
        if self.min_lng is not None and self.max_lng is not None and self.min_lng > self.max_lng:
            raise ValueError(f"bbox lng: min({self.min_lng}) > max({self.max_lng})")
        return self

    @property
    def has_txn_filters(self) -> bool:
        """거래 레벨 가격/면적 필터가 하나라도 있나(있으면 매칭 거래 EXISTS 요구).

        deal_type별 가격축만 본다: sale=price / jeonse=deposit / monthly=deposit+monthly_rent.
        (rent_type 제약은 rep·매칭에만 적용 — SET을 좁히지 않아 여기 미포함, sale 동작 불변.)
        """
        shared = (self.net_area_min, self.net_area_max, self.deal_since)
        if self.deal_type == "sale":
            axis: tuple[object, ...] = (self.price_min, self.price_max)
        elif self.deal_type == "jeonse":
            axis = (self.deposit_min, self.deposit_max)
        else:  # monthly
            axis = (self.deposit_min, self.deposit_max,
                    self.monthly_rent_min, self.monthly_rent_max)
        return any(v is not None for v in shared + axis)

    @property
    def has_bbox(self) -> bool:
        return self.min_lat is not None
