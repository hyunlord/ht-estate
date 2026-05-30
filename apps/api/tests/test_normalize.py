"""이름·주소 정규화 — 괄호/접미사/차수 보존 + 동 추출."""

from __future__ import annotations

from app.match.normalize import extract_dong, name_numbers, normalize_name


def test_normalize_strips_parentheses() -> None:
    assert normalize_name("현대6차(78~81,83,84,86,87동)") == "현대6차"


def test_normalize_strips_apartment_suffix() -> None:
    assert normalize_name("역삼자이아파트") == "역삼자이"


def test_normalize_removes_separators_and_spaces() -> None:
    assert normalize_name("청담 대림 이-편한세상") == "청담대림이편한세상"


def test_normalize_preserves_cha_number() -> None:
    # 차수는 보존 — 다른 단지를 구분하는 핵심 신호
    assert normalize_name("현대5차") != normalize_name("현대6차")
    assert normalize_name("래미안1차") == "래미안1차"


def test_name_numbers_extracts_digits() -> None:
    assert name_numbers(normalize_name("쌍용대치2")) == {"2"}
    assert name_numbers(normalize_name("은마")) == set()


def test_extract_dong_from_jibun_address() -> None:
    assert extract_dong("서울특별시 강남구 역삼동 711-1 역삼자이아파트") == "역삼동"
    assert extract_dong("서울특별시 강남구 압구정동 489") == "압구정동"


def test_extract_dong_none_when_absent() -> None:
    assert extract_dong(None) is None
    assert extract_dong("주소미상") is None
