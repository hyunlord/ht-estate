"""도로명주소 파싱 + 매칭 키."""

from __future__ import annotations

from app.geo.address import addr_key, key_of, parse_road_addr


def test_parse_basic_road_address() -> None:
    parsed = parse_road_addr("서울특별시 강남구 언주로 420")
    assert parsed is not None
    assert parsed.sido == "서울특별시"
    assert parsed.sigungu == "강남구"
    assert parsed.road == "언주로"
    assert parsed.bonbun == 420
    assert parsed.bubun == 0
    assert parsed.underground is False


def test_parse_bonbun_bubun() -> None:
    parsed = parse_road_addr("서울특별시 강남구 테헤란로 152-3")
    assert parsed is not None
    assert parsed.bonbun == 152
    assert parsed.bubun == 3


def test_parse_underground() -> None:
    parsed = parse_road_addr("서울특별시 강남구 테헤란로 지하 152")
    assert parsed is not None
    assert parsed.underground is True
    assert parsed.bonbun == 152


def test_parse_two_token_sigungu() -> None:
    parsed = parse_road_addr("경기도 성남시 분당구 황새울로 200")
    assert parsed is not None
    assert parsed.sigungu == "성남시분당구"
    assert parsed.road == "황새울로"
    assert parsed.bonbun == 200


def test_parse_returns_none_on_garbage() -> None:
    assert parse_road_addr(None) is None
    assert parse_road_addr("주소미상") is None
    assert parse_road_addr("서울특별시 강남구 역삼동 711-1") is None  # 도로명 없음(지번주소)


def test_addr_key_strips_spaces_and_matches() -> None:
    parsed = parse_road_addr("서울특별시 강남구 언주로 420")
    assert parsed is not None
    assert key_of(parsed) == addr_key("서울특별시", "강남구", "언주로", 420, 0)
    assert key_of(parsed) == "서울특별시|강남구|언주로|420-0"
