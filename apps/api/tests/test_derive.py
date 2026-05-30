"""파생 — has_gym 키워드(pos/neg) + parking_ratio(정상·분모 None/0)."""

from __future__ import annotations

import pytest

from app.derive import has_gym, parking_ratio


@pytest.mark.parametrize(
    "amenities",
    [
        "관리사무소, 헬스장, 어린이놀이터",
        "피트니스센터, 독서실",
        "휘트니스클럽",
        "Fitness Center",
        "GYM",
    ],
)
def test_has_gym_positive(amenities: str) -> None:
    assert has_gym(amenities) is True


@pytest.mark.parametrize(
    "amenities",
    [
        "관리사무소, 노인정, 어린이놀이터, 휴게시설",  # 실 K-apt 전형 — gym 없음
        "골프연습장, 탁구장",  # gym 아님
        "",
        None,
    ],
)
def test_has_gym_negative(amenities: str | None) -> None:
    assert has_gym(amenities) is False


def test_parking_ratio_normal() -> None:
    # 역삼자이 실데이터: 615 / 408
    ratio = parking_ratio(615, 408)
    assert ratio is not None
    assert round(ratio, 4) == 1.5074


def test_parking_ratio_zero_household_is_none() -> None:
    assert parking_ratio(100, 0) is None  # 0으로 나누지 않음


def test_parking_ratio_none_inputs() -> None:
    assert parking_ratio(None, 100) is None
    assert parking_ratio(100, None) is None


def test_parking_ratio_zero_parking() -> None:
    assert parking_ratio(0, 100) == 0.0  # 주차 0은 유효값(분모는 정상)
