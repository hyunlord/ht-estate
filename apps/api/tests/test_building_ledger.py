"""건축물대장 클라이언트 파싱 — 표제부 필드·0=미기록 처리·승강기 합산·bun/ji·다중동 매칭. (enrich-1)

라이브 호출 없음(fixture XML). probe(은마 대치동 316)로 확인한 태그 형태 기반.
"""

from __future__ import annotations

from collections.abc import Callable

from app.sources.building_ledger import (
    BuildingLedgerTitle,
    parse_title_info,
    to_bun_ji,
)
from app.store.ledger_repo import pick_match


def _titles(load_fixture: Callable[[str], str]) -> list[BuildingLedgerTitle]:
    return parse_title_info(load_fixture("building_ledger_title.xml"))


def test_parse_title_basic_fields(load_fixture: Callable[[str], str]) -> None:
    titles = _titles(load_fixture)
    assert len(titles) == 2
    a = titles[0]
    assert a.bld_nm == "대치현대빌라가동"
    assert a.structure == "철근콘크리트구조"
    assert a.main_purpose == "공동주택"
    assert a.household_count == 12
    assert a.ground_floor_count == 4
    assert a.basement_floor_count == 1
    assert a.total_floor_area == 1332.57
    assert a.building_coverage_ratio == 59.9
    assert a.floor_area_ratio == 199.5
    assert a.building_height == 13.5
    assert a.approval_date == "2003-08-30"
    assert a.ledger_pk == "102411698"


def test_elevator_summed(load_fixture: Callable[[str], str]) -> None:
    # rideUseElvtCnt(1) + emgenUseElvtCnt(0) = 1
    assert _titles(load_fixture)[0].elevator_count == 1
    # 둘 다 0 → 0(유효: 승강기 없음), 태그는 존재
    assert _titles(load_fixture)[1].elevator_count == 0


def test_zero_continuous_fields_become_none(load_fixture: Callable[[str], str]) -> None:
    # 나동: totArea/bcRat/vlRat/heit가 0 → '미기록'으로 None(0을 사실로 박지 않음)
    b = _titles(load_fixture)[1]
    assert b.total_floor_area is None
    assert b.building_coverage_ratio is None
    assert b.floor_area_ratio is None
    assert b.building_height is None
    # 지하층 0은 유효(보존)
    assert b.basement_floor_count == 0


def test_to_bun_ji() -> None:
    assert to_bun_ji("316") == ("0316", "0000")
    assert to_bun_ji("489-1") == ("0489", "0001")
    assert to_bun_ji("?") is None
    assert to_bun_ji(None) is None
    assert to_bun_ji("0") is None  # 무효 필지


def test_pick_match_single(load_fixture: Callable[[str], str]) -> None:
    # 다동이라도 건물명이 매칭되는 1건 선택(공백 무시 부분일치)
    titles = _titles(load_fixture)
    m = pick_match(titles, "대치현대빌라 가동")
    assert m is not None and m.bld_nm == "대치현대빌라가동"


def test_pick_match_ambiguous_returns_none(load_fixture: Callable[[str], str]) -> None:
    # 다동인데 이름 매칭 실패 → None(억지 매칭 금지)
    titles = _titles(load_fixture)
    assert pick_match(titles, "전혀다른빌라") is None


def test_pick_match_single_item_taken() -> None:
    one = [
        BuildingLedgerTitle(
            bld_nm="단독빌라", dong_nm=None, plat_plc=None, structure="철근콘크리트구조",
            main_purpose="공동주택", household_count=4, ho_count=0, ground_floor_count=4,
            basement_floor_count=0, elevator_count=0, total_floor_area=400.0,
            building_coverage_ratio=None, floor_area_ratio=None, building_height=None,
            approval_date="2010-01-01", ledger_pk="1",
        )
    ]
    assert pick_match(one, "이름달라도1동이면선택") is one[0]
