"""지번(번지) 추출·정규화 — MOLIT 본부번/지번 + K-apt 자유서술 주소 양측.

캐논 표현은 0패딩 제거한 (본번, 부번) 정수쌍. 부번 0은 본번만 표기("711").
산 번지는 정규 번지와 번호공간이 달라 매칭하면 오매칭이 되므로 None(정밀도 우선).
"""

from __future__ import annotations

from app.match.jibun import from_kapt_address, from_molit, to_canonical


def test_from_molit_prefers_bonbun_bubun_strips_zero_padding() -> None:
    assert from_molit("0489", "0000", "489") == (489, 0)


def test_from_molit_keeps_bubun() -> None:
    assert from_molit("0711", "0001", "711-1") == (711, 1)


def test_from_molit_falls_back_to_jibun_field() -> None:
    # 구 데이터: bonbun/bubun 없고 jibun 문자열만
    assert from_molit(None, None, "489") == (489, 0)
    assert from_molit(None, None, "489-1") == (489, 1)


def test_from_molit_none_when_no_lot() -> None:
    assert from_molit(None, None, None) is None
    assert from_molit("", "", "") is None
    assert from_molit("0000", "0000", None) is None  # 본번 0 = 무효


def test_from_kapt_address_standard_with_bubun() -> None:
    assert from_kapt_address("서울특별시 강남구 역삼동 711-1 역삼자이아파트") == (711, 1)


def test_from_kapt_address_no_bubun() -> None:
    assert from_kapt_address("서울특별시 강남구 압구정동 414") == (414, 0)


def test_from_kapt_address_bunji_suffix() -> None:
    assert from_kapt_address("서울특별시 강남구 수서동 750번지") == (750, 0)


def test_from_kapt_address_ignores_name_embedded_numbers() -> None:
    # 동 뒤 숫자토큰만 — 단지명 "래미안3차"의 3을 잡지 않는다
    assert from_kapt_address("서울특별시 강남구 역삼동 711 래미안3차") == (711, 0)
    # 단지명이 번지 앞에 와도 숫자토큰만 골라냄
    assert from_kapt_address("서울특별시 강남구 역삼동 역삼래미안3차 711") == (711, 0)


def test_from_kapt_address_trailing_dash_bonbun() -> None:
    # K-apt가 빈 부번을 "본번-"로 렌더("…삼성동 126- 삼성파크아파트") — 부번 없음 = (본번, 0).
    # 이 파싱 실패가 단지 지번 커버리지를 깎아 지번 회수를 막던 버그(P2-4).
    assert from_kapt_address("서울특별시 강남구 삼성동 126- 삼성파크아파트") == (126, 0)
    assert from_kapt_address("서울특별시 강남구 도곡동 462- 개포럭키아파트") == (462, 0)
    assert from_kapt_address("서울특별시 용산구 한남동 805- 한남동현대하이페리온아파트") == (805, 0)
    # 부번 있는 정상 케이스는 불변(회귀 0)
    assert from_kapt_address("서울특별시 강남구 역삼동 711-1 역삼자이아파트") == (711, 1)


def test_from_kapt_address_san_lot_is_unmatchable() -> None:
    # 산 번지는 번호공간이 달라 정규 번지와 매칭하면 오매칭 → None
    assert from_kapt_address("서울특별시 강남구 개포동 산 1-2 개포자이") is None
    assert from_kapt_address("서울특별시 강남구 개포동 산1-2") is None


def test_from_kapt_address_none_when_unparseable() -> None:
    assert from_kapt_address(None) is None
    assert from_kapt_address("주소미상") is None
    assert from_kapt_address("서울특별시 강남구 역삼동 역삼자이") is None  # 숫자토큰 없음


def test_to_canonical_omits_zero_bubun() -> None:
    assert to_canonical((711, 0)) == "711"
    assert to_canonical((711, 1)) == "711-1"
    assert to_canonical(None) is None


def test_both_sides_agree_on_canonical() -> None:
    # 같은 물리 지번이면 양측 추출이 같은 캐논 문자열로 떨어져야 비교 가능
    molit = to_canonical(from_molit("0711", "0001", "711-1"))
    kapt = to_canonical(from_kapt_address("서울특별시 강남구 역삼동 711-1 역삼자이아파트"))
    assert molit == kapt == "711-1"
