"""K-apt 클라이언트 — 목록/기본정보 파싱(happy/empty/malformed) + fetch(MockTransport)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

import httpx
import pytest

from app.sources.errors import PublicDataError
from app.sources.kapt import (
    fetch_complex_info,
    list_complexes,
    parse_complex_info,
    parse_complex_list,
)

FixtureLoader = Callable[[str], str]


def test_parse_complex_list_happy(load_fixture: FixtureLoader) -> None:
    refs = parse_complex_list(load_fixture("kapt_list.xml"))
    assert [r.kapt_code for r in refs] == ["A13586103", "A13500108"]
    assert refs[0].name == "래미안위브"
    assert refs[0].sido == "서울특별시"
    assert refs[0].sigungu == "강남구"


def test_parse_complex_list_empty(load_fixture: FixtureLoader) -> None:
    assert parse_complex_list(load_fixture("kapt_list_empty.xml")) == []


def test_parse_complex_info_happy(load_fixture: FixtureLoader) -> None:
    info = parse_complex_info(load_fixture("kapt_info.xml"))
    assert info is not None
    assert info.kapt_code == "A13586103"
    assert info.name == "래미안위브"
    assert info.legal_addr == "서울특별시 강남구 도곡동 953"
    assert info.road_addr == "서울특별시 강남구 역삼로 405"
    assert info.approval_date == date(2015, 3, 27)
    assert info.household_count == 1020
    assert info.parking_ground == 320
    assert info.parking_underground == 1140
    assert info.parking_total == 1460  # 지상+지하 합 (파생 아님, 단순합)
    assert info.corridor_type == "계단식"
    assert info.building_type == "철근콘크리트구조"
    assert "헬스장" in (info.amenities_raw or "")  # raw 보존 — has_gym 파싱은 T0-2


def test_parse_complex_info_malformed_is_graceful(load_fixture: FixtureLoader) -> None:
    info = parse_complex_info(load_fixture("kapt_info_malformed.xml"))
    assert info is not None
    assert info.kapt_code == "A99999999"
    assert info.approval_date is None  # 빈 kaptUsedate
    assert info.household_count is None  # 비숫자 kaptdaCnt
    assert info.parking_ground is None  # 누락
    assert info.parking_underground == 200
    assert info.parking_total is None  # 한쪽만 있으면 합 미산출


def test_parse_complex_info_no_item(load_fixture: FixtureLoader) -> None:
    assert parse_complex_info(load_fixture("kapt_info_empty.xml")) is None


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_list_complexes_single_page(load_fixture: FixtureLoader) -> None:
    body = load_fixture("kapt_list.xml")
    client = _client(lambda _r: httpx.Response(200, text=body))
    refs = list_complexes(api_key="dummy", client=client)
    assert len(refs) == 2


def test_fetch_complex_info_happy(load_fixture: FixtureLoader) -> None:
    body = load_fixture("kapt_info.xml")
    info = fetch_complex_info(
        "A13586103", api_key="dummy", client=_client(lambda _r: httpx.Response(200, text=body))
    )
    assert info is not None
    assert info.kapt_code == "A13586103"


def test_fetch_complex_info_propagates_error(load_fixture: FixtureLoader) -> None:
    body = load_fixture("molit_error.xml")  # 시스템 에러 엔벨로프 공용
    client = _client(lambda _r: httpx.Response(200, text=body))
    with pytest.raises(PublicDataError):
        fetch_complex_info("A13586103", api_key="dummy", client=client)
