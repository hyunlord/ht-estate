"""시군구코드 → '시도 시군구' 룩업(geocode 동명중복 해소 토대)."""

from __future__ import annotations

from app.store.regions import sigungu_label


def test_sigungu_label_known_codes() -> None:
    # 실 코드표(data/regions/sigungu_kr.csv) 기반 — 시도+시군구 결합.
    assert sigungu_label("11680") == "서울특별시 강남구"
    assert sigungu_label("11110") == "서울특별시 종로구"
    assert sigungu_label("26110") == "부산광역시 중구"  # '중구' 전국 중복 — 시도로 해소되는 케이스


def test_sigungu_label_unknown_and_none() -> None:
    assert sigungu_label("99999") is None
    assert sigungu_label(None) is None
    assert sigungu_label("") is None
    assert sigungu_label(" 11680 ") == "서울특별시 강남구"  # 공백 트림
