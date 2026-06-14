"""시군구코드 → '시도 시군구' 룩업(geocode 동명중복 해소 토대)."""

from __future__ import annotations

from app.store.regions import canonical_sido, region_by_code, sigungu_label


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


# region-normalize(#6-②)
def test_region_by_code_csv_authoritative() -> None:
    assert region_by_code("11680") == ("서울특별시", "강남구")
    # 통합시 일반구는 CSV가 머지형(공백 없음) — 이게 canonical(스페이스형 아님).
    assert region_by_code("41271") == ("경기도", "안산상록구")
    assert region_by_code("41461") == ("경기도", "용인처인구")
    assert region_by_code("99999") is None
    assert region_by_code(None) is None


def test_canonical_sido_variants_and_passthrough() -> None:
    # 이미 canonical이면 그대로.
    assert canonical_sido("서울특별시") == "서울특별시"
    assert canonical_sido("경기도") == "경기도"
    assert canonical_sido("강원특별자치도") == "강원특별자치도"
    # 도명변경 변종 → CSV canonical.
    assert canonical_sido("강원") == "강원특별자치도"
    assert canonical_sido("전북") == "전북특별자치도"
    # ★미매칭 변종은 None(임의 정규화 금지 — 백필이 NULL 유지·§9 보고).
    assert canonical_sido("듣보광역시") is None
    assert canonical_sido("") is None
    assert canonical_sido(None) is None
