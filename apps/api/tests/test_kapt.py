"""K-apt — 실캡처 JSON(목록·기본·상세) 파싱 + basis/detail 병합 + fetch(MockTransport).

happy fixture(kapt_list/basis/detail.json)는 라이브 실응답 캡처(역삼자이 A10027474 등).
empty/error/sparse/noitem은 엣지 검증용 구성본.
"""

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


def test_parse_complex_list_happy_real_capture(load_fixture: FixtureLoader) -> None:
    refs = parse_complex_list(load_fixture("kapt_list.json"))
    assert [r.kapt_code for r in refs] == ["A10020216", "A10021281", "A10022749"]
    assert refs[0].name == "역삼우정에쉐르2"
    assert refs[0].sido == "서울특별시"
    assert refs[0].sigungu == "강남구"
    assert refs[0].bjd_code == "1168010100"


def test_parse_complex_list_empty(load_fixture: FixtureLoader) -> None:
    assert parse_complex_list(load_fixture("kapt_list_empty.json")) == []


def test_parse_complex_list_raises_on_error(load_fixture: FixtureLoader) -> None:
    with pytest.raises(PublicDataError) as exc_info:
        parse_complex_list(load_fixture("kapt_error.json"))
    assert exc_info.value.result_code == "30"


def test_parse_complex_info_merges_basis_and_detail(load_fixture: FixtureLoader) -> None:
    info = parse_complex_info(load_fixture("kapt_basis.json"), load_fixture("kapt_detail.json"))
    assert info is not None
    assert info.kapt_code == "A10027474"
    assert info.name == "역삼자이아파트"
    # basis 쪽
    assert info.legal_addr == "서울특별시 강남구 역삼동 711-1 역삼자이아파트"
    assert info.road_addr == "서울특별시 강남구 언주로 420"
    assert info.approval_date == date(2016, 6, 22)  # kaptUsedate 20160622
    assert info.household_count == 408  # kaptdaCnt 408.0(float) → int
    assert info.corridor_type == "혼합식"
    # detail 쪽
    assert info.building_type == "철골철근콘크리트구조"  # codeStr
    assert info.parking_ground == 0  # kaptdPcnt '0'(str) → int
    assert info.parking_underground == 615  # kaptdPcntu '615'
    assert info.parking_total == 615  # 합
    assert info.amenities_raw is not None and "관리사무소" in info.amenities_raw
    assert "헬스" not in info.amenities_raw  # 실 K-apt: 헬스장 미기록(라이브 발견)


def test_parse_complex_info_sparse_is_graceful(load_fixture: FixtureLoader) -> None:
    info = parse_complex_info(
        load_fixture("kapt_basis_sparse.json"), load_fixture("kapt_detail_sparse.json")
    )
    assert info is not None
    assert info.kapt_code == "A99999999"
    assert info.approval_date is None  # 빈 kaptUsedate
    assert info.household_count is None  # 비숫자 kaptdaCnt
    assert info.parking_ground is None  # null
    assert info.parking_underground == 200
    assert info.parking_total is None  # 한쪽만 있으면 합 미산출
    assert info.building_type is None  # codeStr "" → None
    assert info.amenities_raw is None


def test_parse_complex_info_no_item(load_fixture: FixtureLoader) -> None:
    noitem = load_fixture("kapt_noitem.json")
    assert parse_complex_info(noitem, noitem) is None


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_list_complexes_single_page(load_fixture: FixtureLoader) -> None:
    body = load_fixture("kapt_list.json")
    client = _client(lambda _r: httpx.Response(200, text=body))
    refs = list_complexes(api_key="dummy", sigungu="11680", client=client, num_of_rows=500)
    assert len(refs) == 3


def test_fetch_complex_info_calls_basis_then_detail(load_fixture: FixtureLoader) -> None:
    basis = load_fixture("kapt_basis.json")
    detail = load_fixture("kapt_detail.json")
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if "BassInfo" in str(req.url):
            seen.append("basis")
            return httpx.Response(200, text=basis)
        seen.append("detail")
        return httpx.Response(200, text=detail)

    info = fetch_complex_info("A10027474", api_key="dummy", client=_client(handler))
    assert info is not None
    assert info.parking_underground == 615
    assert seen == ["basis", "detail"]  # 두 엔드포인트 모두 호출


def test_fetch_complex_info_propagates_error(load_fixture: FixtureLoader) -> None:
    body = load_fixture("kapt_error.json")
    client = _client(lambda _r: httpx.Response(200, text=body))
    with pytest.raises(PublicDataError):
        fetch_complex_info("A10027474", api_key="dummy", client=client)
