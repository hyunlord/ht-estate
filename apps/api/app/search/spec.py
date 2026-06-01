"""HardFilterSpec — 구조화 hard filter 모델 (이진 in/out 입력).

NL→spec 변환(LLM)은 L3/에이전트 소관(별도 티켓). 여기는 **구조화 spec만** 받는다.
gym 필드는 **없다** — K-apt에 헬스장 데이터가 없어(R1 프로브 0/17) Tier-2 enrichment
소관이다. soft 필터(pet·floorplan·gym)·점수 랭킹도 여기 없음.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, model_validator

Preference = Literal["required", "preferred", "none"]
# 거래유형 축(P2-2): 매매=transaction(price) / 전세·월세=rent_transaction(deposit[+monthly_rent]).
DealType = Literal["sale", "jeonse", "monthly"]


class SoftSpec(BaseModel):
    """soft 선호 — 랭킹(ORDER)만 바꾸고 후보 SET은 절대 안 바꾼다(설계 §7·R1).

    none(기본) → 중립 정렬 유지. required > preferred 가중. demote-not-exclude:
    required 미충족도 제외 0(하위 랭킹). 속성 미존재(floorplan·후기)는 Phase 2/3.
    """

    gym: Preference = "none"
    pet: Preference = "none"


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
