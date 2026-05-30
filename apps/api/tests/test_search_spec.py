"""HardFilterSpec — 필드·범위 정합·bbox·gym 부재(R1)."""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from app.search.spec import HardFilterSpec


def test_spec_has_no_gym_field() -> None:
    # R1: gym은 hard filter에서 제외 — 필드 자체가 없어야 한다
    assert not any("gym" in f.lower() for f in HardFilterSpec.model_fields)


def test_valid_spec_and_props() -> None:
    spec = HardFilterSpec(approval_year_min=2010, net_area_min=80.0, net_area_max=90.0)
    assert spec.has_txn_filters is True  # net_area는 txn 필터
    assert spec.has_bbox is False
    assert spec.limit == 50


def test_complex_only_spec_has_no_txn_filters() -> None:
    spec = HardFilterSpec(parking_ratio_gte=1.3)
    assert spec.has_txn_filters is False


@pytest.mark.parametrize(
    "kwargs",
    [
        {"approval_year_min": 2020, "approval_year_max": 2010},
        {"net_area_min": 100.0, "net_area_max": 50.0},
        {"price_min": 200000, "price_max": 100000},
        {"household_count_min": 500, "household_count_max": 100},
    ],
)
def test_range_incoherence_rejected(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        HardFilterSpec(**kwargs)  # type: ignore[arg-type]


def test_bbox_all_or_nothing() -> None:
    with pytest.raises(ValidationError):
        HardFilterSpec(min_lat=37.4, max_lat=37.6, min_lng=127.0)  # max_lng 누락
    # 4개 모두 → OK
    spec = HardFilterSpec(min_lat=37.4, max_lat=37.6, min_lng=127.0, max_lng=127.1)
    assert spec.has_bbox is True


def test_bbox_min_max_order() -> None:
    with pytest.raises(ValidationError):
        HardFilterSpec(min_lat=37.6, max_lat=37.4, min_lng=127.0, max_lng=127.1)


def test_limit_bounds() -> None:
    with pytest.raises(ValidationError):
        HardFilterSpec(limit=0)
    with pytest.raises(ValidationError):
        HardFilterSpec(limit=500)


def test_deal_since_accepts_date() -> None:
    spec = HardFilterSpec(deal_since=date(2025, 4, 1))
    assert spec.has_txn_filters is True
